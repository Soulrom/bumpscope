from typer.testing import CliRunner

from bumpscope.cli import app

runner = CliRunner()


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
    assert "1 breaking changes" in result.output


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
