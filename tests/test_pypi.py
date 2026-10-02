import httpx
import pytest

from bumpscope import pypi


def _fake_get(monkeypatch, status_code: int, files: list[dict] | None = None) -> None:
    def get(url: str, **kwargs) -> httpx.Response:
        return httpx.Response(
            status_code, json={"urls": files or []}, request=httpx.Request("GET", url)
        )

    monkeypatch.setattr(pypi.httpx, "get", get)


def _file(filename: str, packagetype: str = "bdist_wheel") -> dict:
    return {"filename": filename, "packagetype": packagetype, "url": f"https://files/{filename}"}


def test_top_level_modules(tmp_path):
    (tmp_path / "yaml").mkdir()
    (tmp_path / "yaml" / "__init__.py").touch()
    (tmp_path / "_yaml.py").touch()
    (tmp_path / "not_a_package").mkdir()
    (tmp_path / "PyYAML-6.0.dist-info").mkdir()
    (tmp_path / "PyYAML-6.0.data").mkdir()

    assert pypi.top_level_modules(tmp_path) == ["_yaml", "yaml"]


def test_wheel_url_prefers_pure_python_wheel(monkeypatch):
    _fake_get(
        monkeypatch,
        200,
        [
            _file("demo-1.0.tar.gz", packagetype="sdist"),
            _file("demo-1.0-cp313-cp313-win_amd64.whl"),
            _file("demo-1.0-py3-none-any.whl"),
        ],
    )

    assert pypi._wheel_url("demo", "1.0") == "https://files/demo-1.0-py3-none-any.whl"


def test_wheel_url_falls_back_to_platform_wheel(monkeypatch):
    _fake_get(monkeypatch, 200, [_file("demo-1.0-cp313-cp313-win_amd64.whl")])

    assert pypi._wheel_url("demo", "1.0") == "https://files/demo-1.0-cp313-cp313-win_amd64.whl"


def test_wheel_url_unknown_version(monkeypatch):
    _fake_get(monkeypatch, 404)

    with pytest.raises(pypi.PackageNotFoundError, match="not found"):
        pypi._wheel_url("demo", "9.9")


def test_wheel_url_without_wheel(monkeypatch):
    _fake_get(monkeypatch, 200, [_file("demo-1.0.tar.gz", packagetype="sdist")])

    with pytest.raises(pypi.PackageNotFoundError, match="no wheel"):
        pypi._wheel_url("demo", "1.0")
