import io
import os
import subprocess
import sys

import httpx
from rich.console import Console
from typer.testing import CliRunner

from bumpscope import cli
from bumpscope.cli import app

runner = CliRunner()


def test_spinner_works_on_a_terminal_that_cannot_encode_it(publish, monkeypatch):
    # On Windows, `bumpscope ... > NUL` looks like a cp1252 terminal. The braille spinner
    # used to crash there with UnicodeEncodeError instead of finishing.
    publish("1.0", {"demo/__init__.py": "def get(url): ...\n"})
    publish("2.0", {"demo/__init__.py": "def get(url): ...\n"})
    stream = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    monkeypatch.setattr(cli, "err_console", Console(file=stream, force_terminal=True))

    result = runner.invoke(app, ["diff", "demo", "1.0", "2.0"])

    assert result.exception is None
    assert result.exit_code == 0


def test_diff_prints_changes_grouped_by_kind(publish):
    publish("1.0", {"demo/__init__.py": "def get(url, proxies=None): ...\ndef gone(): ...\n"})
    publish("2.0", {"demo/__init__.py": "def get(url): ...\n"})

    result = runner.invoke(app, ["diff", "demo", "1.0", "2.0"])

    assert result.exit_code == 1
    assert "demo 1.0 -> 2.0" in result.stdout
    assert "PARAMETER WAS REMOVED\n  demo.get(proxies)" in result.stdout
    assert "PUBLIC OBJECT WAS REMOVED\n  demo.gone" in result.stdout
    assert "2 breaking changes" in result.stdout


def test_diff_prints_one_line_per_public_path(publish):
    init = "from demo._impl import get\nfrom demo._impl import get as fetch\n"
    init += "__all__ = ['get', 'fetch']\n"
    publish("1.0", {"demo/__init__.py": init, "demo/_impl.py": "def get(url, proxies=None): ..."})
    publish("2.0", {"demo/__init__.py": init, "demo/_impl.py": "def get(url): ..."})

    result = runner.invoke(app, ["diff", "demo", "1.0", "2.0"])

    assert "  demo.fetch(proxies)\n  demo.get(proxies)\n" in result.stdout
    assert "1 breaking change\n" in result.stdout


def test_diff_without_changes(publish):
    publish("1.0", {"demo/__init__.py": "def get(url): ...\n"})
    publish("2.0", {"demo/__init__.py": "def get(url): ...\n"})

    result = runner.invoke(app, ["diff", "demo", "1.0", "2.0"])

    assert result.exit_code == 0
    assert "No breaking API changes found." in result.stdout


def test_diff_unknown_version_is_an_error_on_stderr(publish):
    publish("1.0", {"demo/__init__.py": ""})

    result = runner.invoke(app, ["diff", "demo", "1.0", "9.9"])

    assert result.exit_code == 2
    assert result.stdout == ""
    assert "demo 9.9 was not found on PyPI" in result.stderr


OLD = {"demo/__init__.py": "def get(url, proxies=None): ...\ndef gone(): ...\n"}
NEW = {"demo/__init__.py": "def get(url): ...\n"}


def test_check_reports_impacts(publish, make_project):
    publish("1.0", OLD)
    publish("2.0", NEW)
    root = make_project(
        {
            "app/client.py": "import demo\n\ndemo.get('u', proxies=None)\ndemo.get('u')\n",
            "app/old.py": "from demo import gone\n",
        },
        installed={"demo": "1.0"},
    )

    result = runner.invoke(app, ["check", "demo", "--project", str(root)])

    assert result.exit_code == 1
    assert result.stdout == (
        "1 update available\n"
        "\n"
        "AFFECTS YOUR CODE (1)\n"
        "  demo 1.0 -> 2.0\n"
        "    PARAMETER WAS REMOVED\n"
        "      app/client.py:3  demo.get(proxies)\n"
        "    PUBLIC OBJECT WAS REMOVED\n"
        "      app/old.py:1  demo.gone\n"
        "\n"
        "(method calls on instances are not analyzed)\n"
    )


def test_check_without_impacts_never_says_safe(publish, make_project):
    publish("1.0", OLD)
    publish("2.0", NEW)
    root = make_project({"app.py": "import demo\n\ndemo.get('u')\n"}, installed={"demo": "1.0"})

    result = runner.invoke(app, ["check", "demo", "--project", str(root)])

    assert result.exit_code == 0
    assert result.stdout == (
        "1 update available\n"
        "\n"
        "NO IMPACT FOUND (1)\n"
        "  demo 1.0 -> 2.0\n"
        "\n"
        "(method calls on instances are not analyzed)\n"
    )
    assert "safe" not in result.output.lower()


def test_check_without_breaking_changes_is_no_impact(publish, make_project):
    publish("1.0", NEW)
    publish("2.0", NEW)
    root = make_project({"app.py": "import demo\n"}, installed={"demo": "1.0"})

    result = runner.invoke(app, ["check", "demo", "--project", str(root)])

    assert result.exit_code == 0
    assert "NO IMPACT FOUND (1)\n  demo 1.0 -> 2.0\n" in result.stdout


def test_check_with_explicit_versions_needs_no_venv(publish, make_project):
    publish("1.0", OLD)
    publish("2.0", NEW)
    publish("3.0", NEW)
    root = make_project({"app.py": "from demo import gone\n"}, installed=None)

    result = runner.invoke(
        app, ["check", "demo", "--project", str(root), "--from", "1.0", "--to", "2.0"]
    )

    assert result.exit_code == 1
    assert "  demo 1.0 -> 2.0\n" in result.stdout


def test_check_version_missing_on_pypi_is_unchecked(publish, make_project):
    publish("1.0", OLD)
    root = make_project({"app.py": ""}, installed={"demo": "1.0"})

    result = runner.invoke(app, ["check", "demo", "--project", str(root), "--to", "9.9"])

    assert result.exit_code == 2
    assert "  demo 1.0 -> 9.9: demo 9.9 was not found on PyPI\n" in result.stdout
    assert "Error" not in result.stderr


def test_check_without_venv_is_an_error_on_stderr(publish, make_project):
    publish("2.0", NEW)
    root = make_project({"app.py": ""}, installed=None)

    result = runner.invoke(app, ["check", "demo", "--project", str(root)])

    assert result.exit_code == 2
    assert result.stdout == ""
    assert "No .venv found" in result.stderr
    assert "--from" in result.stderr


def test_check_already_at_target_version(publish, make_project):
    publish("2.0", NEW)
    root = make_project({"app.py": ""}, installed={"demo": "2.0"})

    result = runner.invoke(app, ["check", "demo", "--project", str(root)])

    assert result.exit_code == 0
    assert result.stdout == (
        "0 updates available\n"
        "\n"
        "1 package up to date\n"
        "(method calls on instances are not analyzed)\n"
    )


def test_check_network_error_is_unchecked_not_a_crash(publish, make_project, monkeypatch):
    from bumpscope import pypi

    def offline(package: str) -> str:
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(pypi, "latest_version", offline)
    root = make_project({"app.py": ""}, installed={"demo": "1.0"})

    result = runner.invoke(app, ["check", "demo", "--project", str(root)])

    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert result.exit_code == 2
    assert "COULD NOT CHECK (1)\n  demo 1.0: ConnectError: connection refused\n" in result.stdout


def test_check_equal_versions_spelled_differently_are_up_to_date(publish, make_project):
    publish("2.0", NEW)
    root = make_project({"app.py": ""}, installed={"demo": "2.0"})

    result = runner.invoke(app, ["check", "demo", "--project", str(root), "--to", "2.0.0"])

    assert result.exit_code == 0
    assert "1 package up to date\n" in result.stdout


GIT = {"demo": {"url": "https://git.example/demo", "vcs_info": {"vcs": "git"}}}


def test_check_package_not_installed_from_pypi_is_unchecked(publish, make_project):
    publish("1.0", OLD)
    publish("2.0", NEW)
    root = make_project({"app.py": "from demo import gone\n"}, {"demo": "1.0"}, direct_urls=GIT)

    result = runner.invoke(app, ["check", "demo", "--project", str(root)])

    assert result.exit_code == 2
    assert "COULD NOT CHECK (1)\n  demo 1.0: not installed from PyPI\n" in result.stdout


def test_check_not_from_pypi_holds_with_only_to(publish, make_project):
    publish("1.0", OLD)
    publish("2.0", NEW)
    root = make_project({"app.py": "from demo import gone\n"}, {"demo": "1.0"}, direct_urls=GIT)

    result = runner.invoke(app, ["check", "demo", "--project", str(root), "--to", "2.0"])

    assert result.exit_code == 2
    assert "  demo 1.0 -> 2.0: not installed from PyPI\n" in result.stdout


def test_check_explicit_from_skips_the_pypi_origin_rule(publish, make_project):
    publish("1.0", OLD)
    publish("2.0", NEW)
    root = make_project({"app.py": "from demo import gone\n"}, {"demo": "1.0"}, direct_urls=GIT)

    result = runner.invoke(app, ["check", "demo", "--project", str(root), "--from", "1.0"])

    assert result.exit_code == 1
    assert "AFFECTS YOUR CODE (1)" in result.stdout


def test_check_editable_package_is_not_compared_against_pypi(publish, make_project):
    publish("2.0", NEW)
    editable = {"demo": {"url": "file:///src/demo", "dir_info": {"editable": True}}}
    root = make_project({"app.py": ""}, {"demo": "1.0"}, direct_urls=editable)

    result = runner.invoke(app, ["check", "demo", "--project", str(root)])

    assert result.exit_code == 2
    assert "  demo 1.0: not installed from PyPI\n" in result.stdout


def test_markdown_survives_a_redirected_stdout_with_a_legacy_encoding(make_project):
    # On Windows a redirected stdout uses the locale encoding, e.g. cp1251, which has no "→".
    # Runs in a real process, because the encoding comes from how Python opens stdout.
    root = make_project({"app.py": ""}, installed={"demo": "1.0"}, direct_urls=GIT)
    command = (
        "from bumpscope.cli import app; "
        f"app(['check', 'demo', '--project', {str(root)!r}, '--to', '2.0', '--format', 'markdown'])"
    )

    result = subprocess.run(
        [sys.executable, "-c", command],
        capture_output=True,
        env={**os.environ, "PYTHONIOENCODING": "cp1251"},
    )

    assert result.returncode == 2, result.stderr.decode(errors="replace")
    assert "- `demo` 1.0 → 2.0: not installed from PyPI" in result.stdout.decode("utf-8")
    # Plain "\n" line endings on every platform, so the report file is the same everywhere.
    assert b"\r\n" not in result.stdout
