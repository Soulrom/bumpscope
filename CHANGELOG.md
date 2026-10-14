# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- `check PACKAGE` prints the new report layout, which a run over many packages will share:
  an update count, then the sections AFFECTS YOUR CODE (by package, then by change kind),
  COULD NOT CHECK, NO IMPACT FOUND, an up-to-date count, and the limitation note, which is
  now always printed.
- A package that cannot be compared is reported under COULD NOT CHECK with the reason and
  exit code 2, instead of as an error: a version missing on PyPI, a network failure, a broken
  wheel or a parse error. Before, anything but a missing version crashed with a traceback and
  exit code 1, which read as "impacts found".
- Progress and error messages go to stderr; the report stays on stdout, for both `diff` and
  `check`. Redirecting stdout now captures only the report.

### Fixed

- `check` treats versions spelled differently as the same version (`2.0` and `2.0.0`), so the
  package counts as up to date instead of being downloaded and compared with itself.

## [0.1.0] - 2026-10-14

First release. Published on GitHub only, not on PyPI.

### Added

- `bumpscope diff PACKAGE OLD NEW` lists the breaking API changes between two versions of a
  package. Both versions are downloaded from PyPI as wheels and compared statically with
  griffe, without installing or running them.
- Changes are named by the paths users import them from (`httpx.get(proxies)`), not by where
  they are defined (`httpx._api.get`). A change to `__init__` is shown as the class call
  (`httpx.Client(proxies)`).
- `bumpscope check PACKAGE` reports the places in a project's code that the breaking changes of
  an update touch, with file and line.
  - The installed version is read from the project's `.venv`; `--from` overrides it.
  - The target version is the latest stable release on PyPI, skipping pre-releases and yanked
    releases; `--to` overrides it.
  - `--project` sets the project to check; the default is the current directory.
  - Usages are found through import bindings, including aliases and `from` imports. Each kind
    of change has its own match rule; for example, a removed parameter only counts when a call
    passes it.
  - When nothing matches, `check` says "none matched in your code", never that an update is safe.
- Exit codes for both commands: `0` nothing found, `1` changes or impacts found, `2` error.
- Domain glossary (`CONTEXT.md`) and the v0.1 design (`docs/design/check-v0.1.md`).
- Test suite that runs offline, with PyPI tests behind the `network` marker, and CI on Ubuntu
  and Windows with Python 3.12 and 3.13.

### Fixed

- Redirecting output to `NUL` on Windows (or using an old cp1252 console) no longer crashes
  with `UnicodeEncodeError`: the spinner falls back to ASCII when the console cannot encode it.

### Known limitations

- Method calls on instances are not analyzed (`c = httpx.Client(); c.get(...)`), so `check`
  can miss impacts.
- Only breaking API changes are detected, not deprecations or behavior changes.
- One package per run.

[Unreleased]: https://github.com/Soulrom/bumpscope/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Soulrom/bumpscope/releases/tag/v0.1.0
