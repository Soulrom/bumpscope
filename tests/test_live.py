"""Checks against the real PyPI. Skipped by default; run with `pytest -m network`."""

import pytest

from bumpscope import apidiff


@pytest.mark.network
def test_httpx_proxies_parameter_was_removed():
    changes = apidiff.diff("httpx", "0.24.1", "0.28.0")

    removed = {c.path for c in changes if c.kind == "Parameter was removed"}
    assert any(path.endswith(".get(proxies)") for path in removed)
    assert any(path.endswith("Client.__init__(proxies)") for path in removed)
