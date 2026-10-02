from collections.abc import Callable
from pathlib import Path

import pytest

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

    monkeypatch.setattr(pypi, "download", _download)
    return _publish
