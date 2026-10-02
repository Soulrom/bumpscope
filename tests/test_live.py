"""Checks against the real PyPI. Skipped by default; run with `pytest -m network`."""

import pytest

from bumpscope import apidiff


@pytest.mark.network
def test_httpx_proxies_parameter_was_removed():
    changes = apidiff.diff("httpx", "0.24.1", "0.28.0")

    removed = {label for c in changes if c.kind == "Parameter was removed" for label in c.labels()}
    assert "httpx.get(proxies)" in removed
    assert "httpx.Client(proxies)" in removed
