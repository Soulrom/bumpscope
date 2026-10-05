import io

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
    monkeypatch.setattr(cli, "console", Console(file=stream, force_terminal=True))

    result = runner.invoke(app, ["diff", "demo", "1.0", "2.0"])

    assert result.exception is None
    assert result.exit_code == 0


def test_diff_prints_changes_grouped_by_kind(publish):
    publish("1.0", {"demo/__init__.py": "def get(url, proxies=None): ...\ndef gone(): ...\n"})
    publish("2.0", {"demo/__init__.py": "def get(url): ...\n"})

    result = runner.invoke(app, ["diff", "demo", "1.0", "2.0"])

    assert result.exit_code == 1
    assert "demo 1.0 -> 2.0" in result.output
    assert "PARAMETER WAS REMOVED\n  demo.get(proxies)" in result.output
    assert "PUBLIC OBJECT WAS REMOVED\n  demo.gone" in result.output
    assert "2 breaking changes" in result.output


def test_diff_prints_one_line_per_public_path(publish):
    init = "from demo._impl import get\nfrom demo._impl import get as fetch\n"
    init += "__all__ = ['get', 'fetch']\n"
    publish("1.0", {"demo/__init__.py": init, "demo/_impl.py": "def get(url, proxies=None): ..."})
    publish("2.0", {"demo/__init__.py": init, "demo/_impl.py": "def get(url): ..."})

    result = runner.invoke(app, ["diff", "demo", "1.0", "2.0"])

    assert "  demo.fetch(proxies)\n  demo.get(proxies)\n" in result.output
    assert "1 breaking change\n" in result.output


def test_diff_without_changes(publish):
    publish("1.0", {"demo/__init__.py": "def get(url): ...\n"})
    publish("2.0", {"demo/__init__.py": "def get(url): ...\n"})

    result = runner.invoke(app, ["diff", "demo", "1.0", "2.0"])

    assert result.exit_code == 0
    assert "No breaking API changes found." in result.output


def test_diff_unknown_version(publish):
    publish("1.0", {"demo/__init__.py": ""})

    result = runner.invoke(app, ["diff", "demo", "1.0", "9.9"])

    assert result.exit_code == 2
    assert "demo 9.9 was not found on PyPI" in result.output


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
    assert "demo 1.0 -> 2.0" in result.output
    assert "PARAMETER WAS REMOVED\n  app/client.py:3  demo.get(proxies)\n" in result.output
    assert "PUBLIC OBJECT WAS REMOVED\n  app/old.py:1  demo.gone\n" in result.output
    assert "2 impacts from 2 breaking changes" in result.output


def test_check_counts_are_singular_for_one(publish, make_project):
    publish("1.0", {"demo/__init__.py": "def gone(): ...\n"})
    publish("2.0", {"demo/__init__.py": ""})
    root = make_project({"app.py": "from demo import gone\n"}, installed={"demo": "1.0"})

    result = runner.invoke(app, ["check", "demo", "--project", str(root)])

    assert "1 impact from 1 breaking change (" in result.output
    assert "method calls on instances are not analyzed" in result.output


def test_check_without_impacts_never_says_safe(publish, make_project):
    publish("1.0", OLD)
    publish("2.0", NEW)
    root = make_project({"app.py": "import demo\n\ndemo.get('u')\n"}, installed={"demo": "1.0"})

    result = runner.invoke(app, ["check", "demo", "--project", str(root)])

    assert result.exit_code == 0
    assert (
        "demo 1.0 -> 2.0: 2 breaking changes, none matched in your code "
        "(method calls on instances are not analyzed)"
    ) in result.output
    assert "safe" not in result.output.lower()


def test_check_with_explicit_versions_needs_no_venv(publish, make_project):
    publish("1.0", OLD)
    publish("2.0", NEW)
    publish("3.0", NEW)
    root = make_project({"app.py": "from demo import gone\n"}, installed=None)

    result = runner.invoke(
        app, ["check", "demo", "--project", str(root), "--from", "1.0", "--to", "2.0"]
    )

    assert result.exit_code == 1
    assert "demo 1.0 -> 2.0" in result.output


def test_check_without_venv(publish, make_project):
    publish("2.0", NEW)
    root = make_project({"app.py": ""}, installed=None)

    result = runner.invoke(app, ["check", "demo", "--project", str(root)])

    assert result.exit_code == 2
    assert "No .venv found" in result.output
    assert "--from" in result.output


def test_check_already_at_target_version(publish, make_project):
    publish("2.0", NEW)
    root = make_project({"app.py": ""}, installed={"demo": "2.0"})

    result = runner.invoke(app, ["check", "demo", "--project", str(root)])

    assert result.exit_code == 0
    assert "demo 2.0 is already the target version." in result.output
