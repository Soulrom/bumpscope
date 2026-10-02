import zipfile
from pathlib import Path

import httpx

PYPI_URL = "https://pypi.org/pypi/{package}/{version}/json"


class PackageNotFoundError(Exception):
    """The package or version does not exist on PyPI, or has no wheel."""


def _wheel_url(package: str, version: str) -> str:
    response = httpx.get(PYPI_URL.format(package=package, version=version), timeout=30)
    if response.status_code == 404:
        raise PackageNotFoundError(f"{package} {version} was not found on PyPI")
    response.raise_for_status()

    wheels = [f for f in response.json()["urls"] if f["packagetype"] == "bdist_wheel"]
    if not wheels:
        raise PackageNotFoundError(f"{package} {version} has no wheel on PyPI")

    # A pure-Python wheel contains all the source we need. Prefer it when present.
    pure = [w for w in wheels if w["filename"].endswith("-none-any.whl")]
    return (pure or wheels)[0]["url"]


def download(package: str, version: str, target: Path) -> Path:
    """Download and unpack a wheel into `target`. Returns the unpacked directory."""
    url = _wheel_url(package, version)
    target.mkdir(parents=True, exist_ok=True)
    wheel_path = target / "package.whl"

    with httpx.stream("GET", url, timeout=60, follow_redirects=True) as response:
        response.raise_for_status()
        with wheel_path.open("wb") as file:
            for chunk in response.iter_bytes():
                file.write(chunk)

    unpacked = target / "unpacked"
    with zipfile.ZipFile(wheel_path) as archive:
        archive.extractall(unpacked)
    return unpacked


def top_level_modules(unpacked: Path) -> list[str]:
    """Find importable names in an unpacked wheel, e.g. `yaml` for the PyYAML package."""
    names = []
    for entry in sorted(unpacked.iterdir()):
        if entry.name.endswith((".dist-info", ".data")):
            continue
        if entry.is_dir() and (entry / "__init__.py").exists():
            names.append(entry.name)
        elif entry.suffix == ".py":
            names.append(entry.stem)
    return names
