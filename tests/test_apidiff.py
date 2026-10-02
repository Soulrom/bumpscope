from bumpscope import apidiff
from bumpscope.apidiff import Change


def test_removed_parameter(publish):
    publish("1.0", {"demo/__init__.py": "def get(url, proxies=None): ...\n"})
    publish("2.0", {"demo/__init__.py": "def get(url): ...\n"})

    assert apidiff.diff("demo", "1.0", "2.0") == [
        Change(kind="Parameter was removed", path="demo.get(proxies)")
    ]


def test_removed_module(publish):
    publish("1.0", {"demo/__init__.py": "", "extra.py": "X = 1\n"})
    publish("2.0", {"demo/__init__.py": ""})

    assert apidiff.diff("demo", "1.0", "2.0") == [Change(kind="Module was removed", path="extra")]


def test_version_attribute_is_ignored(publish):
    publish("1.0", {"demo/__init__.py": "__version__ = '1.0'\n"})
    publish("2.0", {"demo/__init__.py": "__version__ = '2.0'\n"})

    assert apidiff.diff("demo", "1.0", "2.0") == []


def test_no_changes(publish):
    source = {"demo/__init__.py": "def get(url): ...\n"}
    publish("1.0", source)
    publish("2.0", source)

    assert apidiff.diff("demo", "1.0", "2.0") == []


def test_changes_are_sorted_by_kind_then_path(publish):
    publish(
        "1.0",
        {"demo/__init__.py": "def b(x): ...\ndef a(x): ...\ndef gone(): ...\n"},
    )
    publish("2.0", {"demo/__init__.py": "def b(): ...\ndef a(): ...\n"})

    changes = apidiff.diff("demo", "1.0", "2.0")

    assert changes == sorted(changes, key=lambda c: (c.kind, c.path))
    assert [c.path for c in changes] == ["demo.a(x)", "demo.b(x)", "demo.gone"]
