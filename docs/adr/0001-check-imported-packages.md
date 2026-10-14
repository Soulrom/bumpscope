---
status: accepted
---

# A full check covers imported packages, not declared dependencies

`bumpscope check` with no package decides what to check by reading the project's code, not its
dependency files. It scans the project's imports and checks the packages installed in the
project's `.venv` that provide those modules (Imported packages). A breaking change can only
break code that imports the package, and imports are the same however the project declares its
dependencies (`pyproject.toml`, `requirements.txt`, Poetry, uv, …).

Packages installed from git, a URL or a local path are not compared against PyPI. PyPI may host
an unrelated package with the same name, and comparing against it would report breaking changes
that do not exist. They are reported as Unchecked packages ("not installed from PyPI") instead.

## Considered options

- **Declared dependencies** (`[project.dependencies]` in `pyproject.toml`): needs a parser per
  dependency format, misses packages the code imports without declaring them (often a
  transitive dependency such as `httpcore`), and checks declared packages the code never imports.
- **Everything installed in `.venv`**: includes dozens of transitive packages the code does not
  touch, which makes runs slow and the report noisy.

## Consequences

- Packages used without an import are not checked: pytest plugins, SQLAlchemy dialects, other
  packages loaded through entry points, and command-line tools run from scripts.
- A declared dependency the code never imports is not checked.
- Packages installed from a private index look like PyPI packages in `.venv` (no
  `direct_url.json`) and are compared against PyPI. This is listed as a limitation in the README.
- Version constraints in dependency files are not read: the target is always the latest stable
  release on PyPI.
