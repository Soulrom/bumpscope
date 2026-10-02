from pathlib import Path

from bumpscope.usages import Usage, find_usages


def _usages(tmp_path: Path, source: str, roots: set[str] | None = None) -> list[Usage]:
    file = tmp_path / "app.py"
    file.write_text(source, encoding="utf-8")
    return find_usages([file], roots or {"httpx"}, tmp_path)


def _calls(usages: list[Usage]) -> list[tuple]:
    return [
        (u.path, u.line, u.positional_count, sorted(u.keywords)) for u in usages if u.kind == "call"
    ]


def test_import_and_call(tmp_path):
    usages = _usages(tmp_path, "import httpx\n\nhttpx.get('u', proxies=None)\n")

    assert usages == [
        Usage("httpx", Path("app.py"), 1),
        Usage("httpx.get", Path("app.py"), 3, "call", 1, frozenset({"proxies"})),
    ]


def test_module_alias(tmp_path):
    usages = _usages(tmp_path, "import httpx as h\nh.Client(app=1)\n")

    assert _calls(usages) == [("httpx.Client", 2, 0, ["app"])]


def test_from_import_with_alias(tmp_path):
    usages = _usages(tmp_path, "from httpx import Client as C\nC('a', 'b')\n")

    assert usages[0] == Usage("httpx.Client", Path("app.py"), 1)
    assert _calls(usages) == [("httpx.Client", 2, 2, [])]


def test_private_import_keeps_the_path_as_written(tmp_path):
    usages = _usages(tmp_path, "from httpx._api import get\nget('u')\n")

    assert _calls(usages) == [("httpx._api.get", 2, 1, [])]


def test_attribute_reference_without_call(tmp_path):
    usages = _usages(tmp_path, "import httpx\nhandler = httpx.Client.send\n")

    assert Usage("httpx.Client.send", Path("app.py"), 2) in usages


def test_unpacking_is_recorded(tmp_path):
    [_, call] = _usages(tmp_path, "import httpx\nhttpx.get(*args, timeout=1, **kw)\n")

    assert call.positional_count == 0
    assert call.keywords == {"timeout"}
    assert call.unpacks_args and call.unpacks_kwargs


def test_calls_inside_arguments_are_found(tmp_path):
    usages = _usages(tmp_path, "import httpx\nprint(httpx.get(httpx.URL('u')))\n")

    assert [c[0] for c in _calls(usages)] == ["httpx.get", "httpx.URL"]


def test_method_calls_on_instances_are_not_resolved(tmp_path):
    # The documented v0.1 gap: `c` is not followed back to `httpx.Client`.
    usages = _usages(tmp_path, "import httpx\nc = httpx.Client()\nc.get('u', proxies=1)\n")

    assert _calls(usages) == [("httpx.Client", 2, 0, [])]


def test_other_packages_and_relative_imports_are_ignored(tmp_path):
    source = "import os\nfrom . import httpx\nos.getcwd()\nhttpx.get('u')\n"

    assert _usages(tmp_path, source) == []


def test_files_that_do_not_parse_are_skipped(tmp_path):
    assert _usages(tmp_path, "def broken(:\n") == []
