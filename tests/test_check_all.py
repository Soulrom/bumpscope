"""`bumpscope check` with no package: every imported package in one run (design decision 9, 10)."""

import pytest
from typer.testing import CliRunner

from bumpscope import apidiff
from bumpscope.cli import app

runner = CliRunner()

DEMO_OLD = {"demo/__init__.py": "def get(url, proxies=None): ...\n"}
DEMO_NEW = {"demo/__init__.py": "def get(url): ...\n"}
RICH_OLD = {"rich/__init__.py": "def show(x): ...\n"}
RICH_NEW = {"rich/__init__.py": "def show(x): ...\ndef extra(): ...\n"}
GIT = {"url": "https://git.example/sdk", "vcs_info": {"vcs": "git"}}


@pytest.fixture
def two_packages(publish):
    publish("1.0", DEMO_OLD, package="demo")
    publish("2.0", DEMO_NEW, package="demo")
    publish("13.0", RICH_OLD, package="rich")
    publish("15.0", RICH_NEW, package="rich")


def _run(root, *args):
    return runner.invoke(app, ["check", "--project", str(root), *args])


def test_checks_every_imported_package(two_packages, make_project):
    root = make_project(
        {"app.py": "import demo\nimport rich\n\ndemo.get('u', proxies=1)\nrich.show(1)\n"},
        installed={"demo": "1.0", "rich": "13.0", "not-imported": "1.0"},
    )

    result = _run(root)

    assert result.exit_code == 1
    assert result.stdout == (
        "2 updates available\n"
        "\n"
        "AFFECTS YOUR CODE (1)\n"
        "  demo 1.0 -> 2.0\n"
        "    PARAMETER WAS REMOVED\n"
        "      app.py:4  demo.get(proxies)\n"
        "\n"
        "NO IMPACT FOUND (1)\n"
        "  rich 13.0 -> 15.0\n"
        "\n"
        "(method calls on instances are not analyzed)\n"
    )


def test_up_to_date_packages_are_counted(two_packages, make_project):
    root = make_project(
        {"app.py": "import demo\nimport rich\n"}, installed={"demo": "2.0", "rich": "13.0"}
    )

    result = _run(root)

    assert result.exit_code == 0
    assert "1 update available\n" in result.stdout
    assert "1 package up to date\n" in result.stdout


def test_one_failing_package_does_not_stop_the_run(two_packages, make_project, monkeypatch):
    real_diff = apidiff.diff

    def flaky(package, old, new):
        if package == "rich":
            raise RuntimeError("griffe could not parse rich")
        return real_diff(package, old, new)

    monkeypatch.setattr(apidiff, "diff", flaky)
    root = make_project(
        {"app.py": "import demo\nimport rich\ndemo.get('u', proxies=1)\n"},
        installed={"demo": "1.0", "rich": "13.0"},
    )

    result = _run(root)

    assert result.exit_code == 1
    assert "AFFECTS YOUR CODE (1)\n  demo 1.0 -> 2.0\n" in result.stdout
    assert (
        "COULD NOT CHECK (1)\n  rich 13.0 -> 15.0: RuntimeError: griffe could not parse rich\n"
    ) in result.stdout


def test_unchecked_package_without_impacts_exits_2(two_packages, make_project):
    root = make_project(
        {"app.py": "import rich\nimport sdk\n"},
        installed={"rich": "13.0", "sdk": "2.1.0"},
        direct_urls={"sdk": GIT},
    )

    result = _run(root)

    assert result.exit_code == 2
    assert "COULD NOT CHECK (1)\n  sdk 2.1.0: not installed from PyPI\n" in result.stdout
    assert "NO IMPACT FOUND (1)\n  rich 13.0 -> 15.0\n" in result.stdout


def test_nothing_found_exits_0(two_packages, make_project):
    root = make_project({"app.py": "import rich\n"}, installed={"rich": "13.0"})

    result = _run(root)

    assert result.exit_code == 0
    assert "NO IMPACT FOUND (1)" in result.stdout


def test_project_without_imported_packages(make_project):
    root = make_project({"app.py": "import os\n"}, installed={"demo": "1.0"})

    result = _run(root)

    assert result.exit_code == 0
    assert result.stdout == "0 updates available\n\n(method calls on instances are not analyzed)\n"


@pytest.mark.parametrize("option", [["--from", "1.0"], ["--to", "2.0"]])
def test_from_or_to_without_a_package_is_a_usage_error(make_project, option):
    root = make_project({"app.py": ""}, installed={})

    result = _run(root, *option)

    assert result.exit_code == 2
    assert result.stdout == ""
    assert "--from and --to need a PACKAGE" in result.stderr


def test_no_venv_is_an_error(make_project):
    root = make_project({"app.py": "import demo\n"}, installed=None)

    result = _run(root)

    assert result.exit_code == 2
    assert result.stdout == ""
    assert "No .venv found" in result.stderr


def test_many_packages_are_all_reported(publish, make_project):
    names = [f"pkg{i}" for i in range(12)]
    for name in names:
        publish("1.0", {f"{name}/__init__.py": "def f(a): ...\n"}, package=name)
        publish("2.0", {f"{name}/__init__.py": "def f(): ...\n"}, package=name)
    source = "".join(f"import {n}\n{n}.f(1)\n" for n in names)
    root = make_project({"app.py": source}, installed=dict.fromkeys(names, "1.0"))

    result = _run(root)

    assert result.exit_code == 1
    assert "AFFECTS YOUR CODE (12)" in result.stdout
    assert all(f"  {n} 1.0 -> 2.0\n" in result.stdout for n in names)


def test_package_without_record_is_unchecked_when_an_import_is_unmatched(make_project):
    root = make_project({"app.py": "import mystery_module\n"}, installed={"mystery": "1.0"})
    for record in root.glob(".venv/**/mystery-1.0.dist-info/RECORD"):
        record.unlink()

    result = _run(root)

    assert result.exit_code == 2
    assert (
        "COULD NOT CHECK (1)\n  mystery 1.0: cannot tell which modules it provides\n"
    ) in result.stdout


def test_each_package_is_matched_only_against_its_own_usages(two_packages, make_project):
    # rich's usage must not be matched against demo's changes, and the other way round.
    root = make_project(
        {"app.py": "import demo\nimport rich\ndemo.get('u', proxies=1)\nrich.show(1)\n"},
        installed={"demo": "1.0", "rich": "13.0"},
    )

    result = _run(root)

    assert "app.py:3  demo.get(proxies)" in result.stdout
    assert "rich.show" not in result.stdout
