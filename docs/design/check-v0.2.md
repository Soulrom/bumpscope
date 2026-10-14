# Design: `bumpscope check` v0.2

This is the source of truth for building v0.2. Read it before starting any step.
Each step is its own branch, PR and GitHub issue in the
[v0.2 milestone](https://github.com/Soulrom/bumpscope/milestone/1); every PR says `Closes #N`.
Terms in **bold** are defined in [`CONTEXT.md`](../../CONTEXT.md).

## Context

v0.1 (`docs/design/check-v0.1.md`, released as `v0.1.0`) checks one package per run and prints
to the terminal. v0.2 checks every **Imported package** of a project in one run and adds a
Markdown report for CI summaries and PR comments.

Existing code to build on:

- `src/bumpscope/apidiff.py`: `diff(package, old, new) -> list[Change]`, `Change.targets()`.
- `src/bumpscope/project.py`: `installed_version`, `python_files`, `ProjectError`.
- `src/bumpscope/usages.py`: `find_usages(files, roots, project) -> list[Usage]`.
- `src/bumpscope/impact.py`: `find_impacts(changes, usages) -> list[Impact]`.
- `src/bumpscope/pypi.py`: `latest_version`, `download`, `PackageNotFoundError`.
- `src/bumpscope/cli.py`: `_status`, `_print_title`, `_print_groups`, `_fail`, exit code constants.

## Settled decisions

### What is checked

1. **Command**: `bumpscope check` with no argument checks every Imported package.
   `bumpscope check PACKAGE` checks one package, as in v0.1.
2. **Imported packages, not declared dependencies**: the packages checked are the ones
   installed in the project's `.venv` that provide a Module the project's code imports. No
   dependency file is read. See [ADR 0001](../adr/0001-check-imported-packages.md).
3. **Module to package mapping**: an imported module is matched to packages by the longest
   path among the files in each package's `RECORD` (`google.cloud.storage` →
   `google/cloud/storage/…` → `google-cloud-storage`; `google.auth` → `google-auth`).
   - If several packages tie on the longest path, all of them are checked.
   - If a package has no `RECORD`, its `top_level.txt` is used instead.
4. **Skipped silently**:
   - standard library modules (`sys.stdlib_module_names`);
   - imported modules that no installed package provides;
   - editable installs (`direct_url.json` with `"dir_info": {"editable": true}`): that is the
     project's own code.
5. **Not installed from PyPI**: a package whose `direct_url.json` has `vcs_info`,
   `archive_info` or a non-editable `dir_info` (git, a URL or a local path) is not compared
   against PyPI, which may host an unrelated package with the same name. It becomes an
   **Unchecked package** with the reason "not installed from PyPI".
   - The rule applies whenever the from-version is read from `.venv`, including when only
     `--to` is given.
   - It is skipped only when `--from` is passed explicitly.
6. **Package with neither `RECORD` nor `top_level.txt`**: becomes an Unchecked package with the
   reason "cannot tell which modules it provides", but only when the code has an unmatched
   import. An import is not unmatched if it is:
   - one of the project's own modules (a file or directory in the project root or in `src/`);
   - a relative import;
   - inside a `try` block whose handlers catch `ImportError` or `ModuleNotFoundError`
     (an optional dependency).
7. **Versions**: the from-version is read from `.venv`; the to-version is the latest stable
   release on PyPI (no pre-releases, no fully yanked releases), as in v0.1.
   `--from` and `--to` are only allowed together with `PACKAGE`; without it they are an error
   with exit code 2.
8. **Up-to-date packages** (installed version equals the to-version) are counted, not listed.

### Running

9. **Concurrency**: packages are checked in a thread pool of 8 workers.
   - Each package gets its own temp directories and its own `griffe.load` calls; threads share
     no state.
   - The project's files are scanned once, before the pool starts; the threads only read the
     result.
   - A failure in one package (network, broken wheel, griffe error, any exception) never stops
     the run: that package becomes an Unchecked package with the reason.
   - No cache. Revisit if a full run on a real project takes more than a minute.
10. **Exit codes** (both modes): `1` if any package has an Impact; otherwise `2` if any package
    could not be checked; otherwise `0`. An Unchecked package never ends a run with `0`.
    Usage errors (`--from` without `PACKAGE`, no `.venv`) are `2`.

### Report

11. **One report** for both modes: `check httpx` is a report with one package. Tests asserting
    the v0.1 output are rewritten, not deleted. The output change is noted under
    `[Unreleased]` in `CHANGELOG.md`.
12. **Section order**, the same for text and Markdown:
    AFFECTS YOUR CODE → COULD NOT CHECK → NO IMPACT FOUND → up-to-date count → limitation note.
    Empty sections are left out; the limitation note is always printed.
13. **Text layout**:

    ```
    14 updates available

    AFFECTS YOUR CODE (1)
      httpx 0.24.1 -> 0.28.0
        PARAMETER WAS REMOVED
          app/client.py:5  httpx.Client(proxies)

    COULD NOT CHECK (1)
      internal-sdk 2.1.0: not installed from PyPI

    NO IMPACT FOUND (12)
      rich 13.7.0 -> 15.0.0, click 8.1.7 -> 8.3.0, ...

    3 packages up to date
    (method calls on instances are not analyzed)
    ```

    Inside AFFECTS YOUR CODE: one block per package, then grouped by change kind, then one line
    per Impact, as in v0.1.
14. **Formats and streams**: `--format text` (default) or `--format markdown`. In both formats
    and for both commands, the report goes to stdout; progress and error messages go to stderr.
15. **Markdown layout**:

    ```markdown
    ## bumpscope: 1 of 14 updates affects your code

    ### Affects your code

    #### httpx 0.24.1 → 0.28.0

    | Location | Change | Kind |
    |---|---|---|
    | `app/client.py:5` | `httpx.Client(proxies)` | Parameter was removed |

    ### Could not check

    - `internal-sdk` 2.1.0: not installed from PyPI

    <details><summary>No impact found (12)</summary>

    rich 13.7.0 → 15.0.0, click 8.1.7 → 8.3.0, ...

    </details>

    3 packages up to date. Method calls on instances are not analyzed.
    ```

    - One table per package with Impacts; `|` inside a cell is escaped as `\|`.
    - COULD NOT CHECK is always expanded; NO IMPACT FOUND is collapsed in `<details>`.
16. **Markdown size cap** of about 60,000 characters (GitHub PR comments allow about 65,000):
    - the title and COULD NOT CHECK are never cut;
    - NO IMPACT FOUND is trimmed first, then Impact rows from the end;
    - the report ends with "and N more";
    - cut only at whole table rows or list items, and always close `<details>`, so the
      Markdown never breaks.
    The text format is never cut.
17. **Markdown output**: printed as is, with no colors, no line wrapping and no spinner. stdout
    is forced to UTF-8, because on Windows a redirected stdout uses the locale encoding
    (e.g. cp1251) and `→` raises `UnicodeEncodeError`. A test covering this runs in Windows CI.

### Documentation

18. README: the v0.2 roadmap item describes checking imported packages, not reading dependency
    files. New limitation: packages from private indexes cannot be told apart from PyPI ones
    and are compared against PyPI.
19. `CHANGELOG.md`: each PR that changes behavior adds a line under `[Unreleased]`.

## Steps

### Step 0: CHANGELOG.md and v0.1.0 release (#3) — done

`CHANGELOG.md` merged in PR #9; tag `v0.1.0` on that merge; GitHub Release created.

### Step 1: design docs (#4)

This file, `docs/adr/0001-check-imported-packages.md`, `CONTEXT.md` (Imported package,
Unchecked package, Dependency clarified), README roadmap and limitations. No code changes.

### Step 2: report model and text renderer (#5)

- New module `src/bumpscope/report.py`:
  - `PackageResult`: package name, from- and to-version, and exactly one outcome: Impacts
    (possibly none), Unchecked with a reason, or up to date.
  - `Report`: the list of `PackageResult`, plus `exit_code()` per decision 10.
  - `render_text(report, console)`: decisions 12 and 13. Reuses `_print_groups` from `cli.py`,
    moved here.
- `cli.check PACKAGE` builds a one-package `Report` and renders it; exit code from
  `Report.exit_code()`.
- stderr: progress (`_status`) and errors (`_fail`) use a second `Console(stderr=True)`, for
  `check` and `diff` (decision 14).
- Rewrite the `check` tests in `tests/test_cli.py` for the new layout; error tests read
  `result.stderr`. Unit tests for `render_text` and `exit_code()` in `tests/test_report.py`.
- `CHANGELOG.md` `[Unreleased]`: new `check` output layout; errors and progress on stderr.

### Step 3: imported packages (#6)

- New module `src/bumpscope/environment.py` (reads the project's `.venv`):
  - `installed_packages(project) -> list[InstalledPackage]`: name, version, the module paths it
    provides (from `RECORD`, else `top_level.txt`, else unknown) and its origin (PyPI,
    editable, not from PyPI) from `direct_url.json`. Reuses the `.venv` lookup and name
    normalisation from `project.installed_version`, which moves here.
- `src/bumpscope/usages.py`: `imported_modules(files, project) -> list[ImportedModule]`: every
  absolute import with its full module path and whether it sits in a `try` that catches
  `ImportError` / `ModuleNotFoundError`.
- `imported_packages(...)` applies decisions 3–6 and returns the packages to check plus the
  Unchecked packages with reasons.
- Tests in `tests/test_environment.py` with fake `.dist-info` directories: longest-path match,
  ties, `top_level.txt` fallback, stdlib, editable, each `direct_url.json` kind, the
  no-`RECORD` rule and each exception from decision 6.
- Single-package mode starts applying decision 5 (from-version read from `.venv`).

### Step 4: all imported packages (#7)

- `cli.check` with no `PACKAGE`: scan the project once, find Imported packages (step 3), get
  each latest version, run `apidiff.diff` and `impact.find_impacts` per package in a
  `ThreadPoolExecutor(max_workers=8)` (decision 9), and render one `Report`.
- Each worker returns a `PackageResult`; any exception becomes Unchecked with the reason.
- `--from` / `--to` without `PACKAGE` → error, exit 2.
- Progress on stderr: `Checking 5/14: httpx`.
- Tests: fake PyPI with several packages, one failing package does not stop the run, exit code
  combinations from decision 10, the usage error.
- Record the run time on a real project in the PR description.

### Step 5: Markdown report (#8)

- `render_markdown(report) -> str` in `report.py`: decisions 15 and 16.
- `--format text|markdown` on `check`; markdown printed with plain `sys.stdout.write`, stdout
  reconfigured to UTF-8 (decision 17).
- Tests: layout, `|` escaping, the size cap (trimming order, whole rows, `<details>` closed,
  "and N more"), and a subprocess test that redirects stdout with a non-UTF-8 encoding
  (`PYTHONIOENCODING=cp1251`) and checks `→` survives; it must run in Windows CI.

## Verification

Every step:

- `uv run ruff check .`, `uv run ruff format --check .` and `uv run pytest` pass.
- CI is green on Linux and Windows.

End to end, after step 5, on a scratch project whose `.venv` holds httpx 0.24.1 and a package
installed from git, with code calling `httpx.Client(proxies=...)`:

- `uv run bumpscope check --project <tmp>` lists the httpx Impact under AFFECTS YOUR CODE and
  the git package under COULD NOT CHECK; exit code 1.
- Remove the call: no Impact, the git package is still unchecked; exit code 2.
- `uv run bumpscope check --project <tmp> --format markdown > report.md` gives valid Markdown
  with `→` intact, on Windows too.
- `uv run bumpscope check --from 1.0` without a package: error on stderr, exit code 2.
