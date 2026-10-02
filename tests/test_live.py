"""Checks against the real PyPI. Skipped by default; run with `pytest -m network`."""

import pytest

from bumpscope import apidiff


@pytest.mark.network
def test_httpx_proxies_parameter_was_removed():
    changes = apidiff.diff("httpx", "0.24.1", "0.28.0")

    removed = {label for c in changes if c.kind == "Parameter was removed" for label in c.labels()}
    assert "httpx.get(proxies)" in removed
    assert "httpx.Client(proxies)" in removed


@pytest.mark.network
def test_check_finds_httpx_client_proxies(tmp_path):
    from typer.testing import CliRunner

    from bumpscope.cli import app

    (tmp_path / "client.py").write_text("import httpx\n\nhttpx.Client(proxies='http://p')\n")

    result = CliRunner().invoke(
        app, ["check", "httpx", "--project", str(tmp_path), "--from", "0.24.1", "--to", "0.28.0"]
    )

    assert result.exit_code == 1
    assert "client.py:3  httpx.Client(proxies)" in result.output
