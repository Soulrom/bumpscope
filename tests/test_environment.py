import json
from pathlib import Path

import pytest

from bumpscope import environment
from bumpscope.environment import (
    NOT_FROM_PYPI,
    UNKNOWN_MODULES,
    InstalledPackage,
    Origin,
    find_installed,
    imported_packages,
    installed_packages,
)
from bumpscope.project import ProjectError
from bumpscope.usages import ImportedModule


def _site_packages(root: Path, windows: bool = False) -> Path:
    if windows:
        path = root / ".venv" / "Lib" / "site-packages"
    else:
        path = root / ".venv" / "lib" / "python3.13" / "site-packages"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _dist(
    site_packages: Path,
    name: str,
    version: str = "1.0",
    record: list[str] | None = None,
    top_level: list[str] | None = None,
    direct_url: dict | None = None,
) -> None:
    """Write a fake `.dist-info`, the way pip or uv would after installing `name`."""
    info = site_packages / f"{name.replace('-', '_')}-{version}.dist-info"
    info.mkdir(parents=True)
    (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n")
    if record is not None:
        lines = [*record, f"{info.name}/METADATA,,", f"{info.name}/RECORD,,"]
        (info / "RECORD").write_text("\n".join(lines) + "\n")
    if top_level is not None:
        (info / "top_level.txt").write_text("\n".join(top_level) + "\n")
    if direct_url is not None:
        (info / "direct_url.json").write_text(json.dumps(direct_url))


def _installed(name, modules, origin=Origin.PYPI, version="1.0"):
    return InstalledPackage(name, version, None if modules is None else frozenset(modules), origin)


def _imports(*paths: str, optional: bool = False) -> list[ImportedModule]:
    return [ImportedModule(path, Path("app.py"), 1, optional) for path in paths]


# Reading the .venv


def test_modules_come_from_record(tmp_path):
    sp = _site_packages(tmp_path)
    _dist(
        sp,
        "PyYAML",
        "6.0.2",
        record=[
            "yaml/__init__.py,sha256=x,1",
            "yaml/loader.py,sha256=x,1",
            "yaml/__pycache__/loader.cpython-313.pyc,,",
            "_yaml/__init__.py,sha256=x,1",
            "yaml/_yaml.cp313-win_amd64.pyd,sha256=x,1",
            "../../Scripts/yaml.exe,sha256=x,1",
        ],
    )

    [package] = installed_packages(tmp_path)

    assert package == InstalledPackage(
        "PyYAML",
        "6.0.2",
        frozenset({"yaml", "yaml.loader", "yaml._yaml", "_yaml"}),
        Origin.PYPI,
    )


def test_record_is_read_as_csv(tmp_path):
    # RECORD may quote paths. Splitting on commas would keep the quotes and lose the module.
    sp = _site_packages(tmp_path)
    _dist(sp, "demo", record=['"demo/mod.py",sha256=x,1', '"demo/data,1.json",,'])

    [package] = installed_packages(tmp_path)

    assert package.modules == {"demo", "demo.mod"}


def test_top_level_txt_is_the_fallback_without_record(tmp_path):
    sp = _site_packages(tmp_path)
    _dist(sp, "demo", top_level=["demo", "_demo_speedups"])

    [package] = installed_packages(tmp_path)

    assert package.modules == {"demo", "_demo_speedups"}


def test_modules_are_unknown_without_record_and_top_level(tmp_path):
    sp = _site_packages(tmp_path)
    _dist(sp, "demo")

    [package] = installed_packages(tmp_path)

    assert package.modules is None


@pytest.mark.parametrize(
    ("direct_url", "origin"),
    [
        (None, Origin.PYPI),
        ({"url": "file:///p", "dir_info": {"editable": True}}, Origin.EDITABLE),
        ({"url": "file:///p", "dir_info": {}}, Origin.OTHER),
        ({"url": "https://x/demo.whl", "archive_info": {}}, Origin.OTHER),
        ({"url": "https://git/demo", "vcs_info": {"vcs": "git"}}, Origin.OTHER),
    ],
    ids=["pypi", "editable", "local-path", "url", "git"],
)
def test_origin_comes_from_direct_url_json(tmp_path, direct_url, origin):
    sp = _site_packages(tmp_path)
    _dist(sp, "demo", record=["demo/__init__.py,,"], direct_url=direct_url)

    [package] = installed_packages(tmp_path)

    assert package.origin == origin


def test_windows_layout(tmp_path):
    sp = _site_packages(tmp_path, windows=True)
    _dist(sp, "demo", record=["demo/__init__.py,,"])

    assert [p.name for p in installed_packages(tmp_path)] == ["demo"]


def test_no_venv(tmp_path):
    with pytest.raises(ProjectError, match="No .venv found.*--from"):
        installed_packages(tmp_path)


def test_find_installed_normalizes_names(tmp_path):
    sp = _site_packages(tmp_path)
    _dist(sp, "PyYAML", "6.0.2", record=["yaml/__init__.py,,"])
    _dist(sp, "my_pkg", "0.3", record=["my_pkg/__init__.py,,"])

    assert find_installed(tmp_path, "pyyaml").version == "6.0.2"
    assert find_installed(tmp_path, "My-Pkg").version == "0.3"


def test_find_installed_package_missing(tmp_path):
    sp = _site_packages(tmp_path)
    _dist(sp, "other", record=[])

    with pytest.raises(ProjectError, match="demo is not installed.*--from"):
        find_installed(tmp_path, "demo")


# Matching imports to packages


def test_longest_path_wins_for_namespace_packages(tmp_path):
    installed = [
        _installed("google-auth", {"google", "google.auth"}),
        _installed("google-cloud-core", {"google", "google.cloud", "google.cloud.client"}),
        _installed("google-cloud-storage", {"google", "google.cloud", "google.cloud.storage"}),
        _installed("protobuf", {"google", "google.protobuf"}),
    ]

    result = imported_packages(
        tmp_path, installed, _imports("google.cloud.storage.Client", "google.auth")
    )

    assert {p.name for p in result.to_check} == {"google-cloud-storage", "google-auth"}
    assert result.unchecked == ()


def test_packages_tied_on_the_longest_path_are_all_checked(tmp_path):
    installed = [_installed("one", {"shared"}), _installed("two", {"shared"})]

    result = imported_packages(tmp_path, installed, _imports("shared.thing"))

    assert {p.name for p in result.to_check} == {"one", "two"}


def test_each_package_is_checked_once(tmp_path):
    installed = [_installed("httpx", {"httpx"})]

    result = imported_packages(tmp_path, installed, _imports("httpx", "httpx.Client", "httpx"))

    assert [p.name for p in result.to_check] == ["httpx"]


def test_stdlib_and_unprovided_modules_are_skipped(tmp_path):
    installed = [_installed("httpx", {"httpx"})]

    result = imported_packages(tmp_path, installed, _imports("os.path", "json", "nowhere"))

    assert result.to_check == ()
    assert result.unchecked == ()


def test_editable_install_is_skipped(tmp_path):
    installed = [_installed("myapp", {"myapp"}, Origin.EDITABLE)]

    result = imported_packages(tmp_path, installed, _imports("myapp.models"))

    assert result.to_check == ()
    assert result.unchecked == ()


def test_package_not_from_pypi_is_unchecked(tmp_path):
    installed = [_installed("internal-sdk", {"internal_sdk"}, Origin.OTHER, "2.1.0")]

    result = imported_packages(tmp_path, installed, _imports("internal_sdk"))

    assert result.to_check == ()
    assert [(p.name, reason) for p, reason in result.unchecked] == [("internal-sdk", NOT_FROM_PYPI)]


def test_package_with_unknown_modules_is_unchecked_only_with_an_unmatched_import(tmp_path):
    installed = [_installed("httpx", {"httpx"}), _installed("mystery", None)]

    matched = imported_packages(tmp_path, installed, _imports("httpx"))
    unmatched = imported_packages(tmp_path, installed, _imports("httpx", "mystery_mod"))

    assert matched.unchecked == ()
    assert [(p.name, r) for p, r in unmatched.unchecked] == [("mystery", UNKNOWN_MODULES)]


@pytest.mark.parametrize("layout", ["root-file", "root-package", "src-package"])
def test_project_own_modules_are_not_unmatched(tmp_path, layout):
    {
        "root-file": lambda: (tmp_path / "helpers.py").write_text(""),
        "root-package": lambda: (tmp_path / "helpers").mkdir(),
        "src-package": lambda: (tmp_path / "src" / "helpers").mkdir(parents=True),
    }[layout]()
    installed = [_installed("mystery", None)]

    result = imported_packages(tmp_path, installed, _imports("helpers.tools"))

    assert result.unchecked == ()


def test_optional_imports_are_not_unmatched(tmp_path):
    installed = [_installed("mystery", None)]

    result = imported_packages(tmp_path, installed, _imports("ujson", optional=True))

    assert result.unchecked == ()


def test_optional_import_of_an_installed_package_is_still_checked(tmp_path):
    installed = [_installed("ujson", {"ujson"})]

    result = imported_packages(tmp_path, installed, _imports("ujson", optional=True))

    assert [p.name for p in result.to_check] == ["ujson"]


def test_stdlib_names_are_known():
    assert {"os", "json", "asyncio"} <= environment.STDLIB_MODULES


# Robustness and edge cases


def test_dist_info_without_name_or_version_is_skipped(tmp_path):
    sp = _site_packages(tmp_path)
    _dist(sp, "demo", record=["demo/__init__.py,,"])
    broken = sp / "broken-1.0.dist-info"
    broken.mkdir()
    (broken / "METADATA").write_text("Metadata-Version: 2.1\n")

    assert [p.name for p in installed_packages(tmp_path)] == ["demo"]
    assert find_installed(tmp_path, "demo").version == "1.0"


def test_unreadable_files_of_one_package_do_not_break_the_others(tmp_path):
    sp = _site_packages(tmp_path)
    _dist(sp, "demo", record=["demo/__init__.py,,"])
    _dist(sp, "weird", record=["weird/__init__.py,,"])
    weird = sp / "weird-1.0.dist-info"
    (weird / "RECORD").write_bytes(b"\xff\xfe\x00broken")
    (weird / "direct_url.json").write_text("{not json")

    packages = {p.name: p for p in installed_packages(tmp_path)}

    assert packages["demo"].modules == {"demo"}
    assert packages["weird"].modules is None
    assert packages["weird"].origin is Origin.OTHER


def test_find_installed_reads_only_the_wanted_package(tmp_path):
    sp = _site_packages(tmp_path)
    _dist(sp, "demo", record=["demo/__init__.py,,"])
    _dist(sp, "other", record=["other/__init__.py,,"])
    (sp / "other-1.0.dist-info" / "direct_url.json").write_text("{not json")

    assert find_installed(tmp_path, "demo").origin is Origin.PYPI


def test_top_level_txt_is_read_as_utf8(tmp_path):
    sp = _site_packages(tmp_path)
    _dist(sp, "demo")
    (sp / "demo-1.0.dist-info" / "top_level.txt").write_bytes("démo\n".encode())

    [package] = installed_packages(tmp_path)

    assert package.modules == {"démo"}


def test_vendored_library_directories_are_not_modules(tmp_path):
    sp = _site_packages(tmp_path)
    _dist(
        sp,
        "numpy",
        record=[
            "numpy/__init__.py,,",
            "numpy.libs/libopenblas-abc.so,,",
            "numpy.libs/msvcp140-def.dll,,",
            "numpy/core/_multiarray_umath.cpython-313-x86_64-linux-gnu.so,,",
        ],
    )

    [package] = installed_packages(tmp_path)

    assert package.modules == {"numpy", "numpy.core", "numpy.core._multiarray_umath"}


def test_project_folder_named_like_a_package_does_not_hide_it(tmp_path):
    # Alembic projects keep migrations in `alembic/`, and still import the alembic package.
    (tmp_path / "alembic").mkdir()
    installed = [_installed("alembic", {"alembic", "alembic.op"})]

    result = imported_packages(tmp_path, installed, _imports("alembic.op"))

    assert [p.name for p in result.to_check] == ["alembic"]


def test_editable_install_without_modules_in_record_matches_by_name(tmp_path):
    # PEP 660 editable installs list only a .pth or finder file in RECORD.
    sp = _site_packages(tmp_path)
    _dist(
        sp,
        "my-lib",
        record=["__editable__.my_lib-1.0.pth,,", "__editable___my_lib_1_0_finder.py,,"],
        direct_url={"url": "file:///elsewhere/my-lib", "dir_info": {"editable": True}},
    )
    installed = installed_packages(tmp_path) + [_installed("mystery", None)]

    result = imported_packages(tmp_path, installed, _imports("my_lib.tools"))

    assert result.to_check == ()
    assert result.unchecked == ()


def test_editable_install_uses_top_level_txt_too(tmp_path):
    sp = _site_packages(tmp_path)
    _dist(
        sp,
        "sibling",
        record=["__editable__.sibling-1.0.pth,,"],
        top_level=["sib"],
        direct_url={"url": "file:///elsewhere/sibling", "dir_info": {"editable": True}},
    )

    [package] = installed_packages(tmp_path)

    assert "sib" in package.modules
