# bumpscope

bumpscope compares what changed between two versions of a Python package with what a project
actually uses, and reports only the overlap.

## Packages

**Package**:
A distribution on PyPI, identified by its name and version, e.g. `PyYAML 6.0.2`.
_Avoid_: library, distribution

**Module**:
An importable top-level name that a Package provides, e.g. `yaml` from PyYAML.

**Dependency**:
A Package that the checked project declares. A full check does not go by Dependencies: it
covers Imported packages, which may include undeclared Packages and leave out declared ones the
code never imports.

**Imported package**:
A Package installed in the checked project's environment that provides a Module the project's
code imports. These are the Packages a full check covers, whether declared or not.

**Unchecked package**:
An Imported package that could not be compared, with the reason, e.g. not on PyPI, no wheel,
or not installed from PyPI. An Unchecked package is never reported as "no impact found".
_Avoid_: skipped, ignored, failed package

**From version**:
The version of a Package installed in the checked project's environment.

**To version**:
The release of a Package being considered as the upgrade target.

## Changes

**Breaking change**:
An API change between the From version and the To version that can break code calling it.
_Avoid_: breakage

**Public path**:
A dotted path users can import an API object from, e.g. `httpx.get`. One object can have several.
_Avoid_: alias

**Definition path**:
The dotted path where an API object is defined, e.g. `httpx._api.get`.

## Project code

**Usage**:
A place in the checked project's code that references a Public path or Definition path.

**Impact**:
A Breaking change that matches a Usage, located at file and line. When no Impacts are found,
the result is "no impact found", never "safe".
_Avoid_: hit, match, finding, overlap
