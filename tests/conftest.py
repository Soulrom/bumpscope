from collections.abc import Callable
from pathlib import Path

import pytest
from packaging.version import Version

from bumpscope import pypi

Publish = Callable[[str, dict[str, str]], None]


@pytest.fixture
def publish(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Publish:
    """Fake PyPI. `publish(version, files)` makes a version whose wheel unpacks to `files`.

    `files` maps paths inside the wheel to source, e.g. {"demo/__init__.py": "def f(a): ..."}.
    """
    published: dict[str, Path] = {}

    def _publish(version: str, files: dict[str, str]) -> None:
        unpacked = tmp_path / "wheels" / version
        for name, source in files.items():
            path = unpacked / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(source, encoding="utf-8")
        published[version] = unpacked

    def _download(package: str, version: str, target: Path) -> Path:
        if version not in published:
            raise pypi.PackageNotFoundError(f"{package} {version} was not found on PyPI")
        return published[version]

    def _latest_version(package: str) -> str:
        return max(published, key=Version)

    monkeypatch.setattr(pypi, "download", _download)
    monkeypatch.setattr(pypi, "latest_version", _latest_version)
    return _publish


MakeProject = Callable[..., Path]


@pytest.fixture
def make_project(tmp_path: Path) -> MakeProject:
    """`make_project(files, installed={"demo": "1.0"})` writes a project with a fake `.venv`.

    `files` maps paths in the project to source. `installed` maps package names to the
    versions recorded in the `.venv`'s dist-info; pass `installed=None` for no `.venv` at all.
    """

    def _make(files: dict[str, str], installed: dict[str, str] | None = None) -> Path:
        root = tmp_path / "project"
        for name, source in files.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(source, encoding="utf-8")
        root.mkdir(exist_ok=True)
        if installed is not None:
            venv = root / ".venv"
            site_packages = venv / "lib" / "python3.13" / "site-packages"
            site_packages.mkdir(parents=True)
            (venv / "pyvenv.cfg").write_text("home = /usr/bin\n")
            for package, version in installed.items():
                dist_info = site_packages / f"{package}-{version}.dist-info"
                dist_info.mkdir(parents=True)
                (dist_info / "METADATA").write_text(
                    f"Metadata-Version: 2.1\nName: {package}\nVersion: {version}\n"
                )
        return root

    return _make
