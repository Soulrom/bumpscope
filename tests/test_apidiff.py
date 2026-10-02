from griffe import ParameterKind

from bumpscope import apidiff
from bumpscope.apidiff import Change, Parameter

REEXPORTING_INIT = "from demo._impl import Client, get\n\n__all__ = ['Client', 'get']\n"


def test_removed_parameter(publish):
    publish("1.0", {"demo/__init__.py": "def get(url, proxies=None): ...\n"})
    publish("2.0", {"demo/__init__.py": "def get(url): ...\n"})

    assert apidiff.diff("demo", "1.0", "2.0") == [
        Change(
            kind="Parameter was removed",
            public_paths=("demo.get",),
            definition_path="demo.get",
            parameter=Parameter(name="proxies", index=1, kind=ParameterKind.positional_or_keyword),
        )
    ]


def test_change_is_named_by_its_public_paths(publish):
    publish(
        "1.0",
        {
            "demo/__init__.py": REEXPORTING_INIT,
            "demo/api.py": "from demo._impl import get\n\n__all__ = ['get']\n",
            "demo/_impl.py": "def get(url, proxies=None): ...\nclass Client: ...\n",
        },
    )
    publish(
        "2.0",
        {
            "demo/__init__.py": REEXPORTING_INIT,
            "demo/api.py": "from demo._impl import get\n\n__all__ = ['get']\n",
            "demo/_impl.py": "def get(url): ...\nclass Client: ...\n",
        },
    )

    [change] = apidiff.diff("demo", "1.0", "2.0")

    assert change.public_paths == ("demo.api.get", "demo.get")
    assert change.definition_path == "demo._impl.get"
    assert change.labels() == ["demo.api.get(proxies)", "demo.get(proxies)"]


def test_wildcard_import_is_followed_without_placeholder_paths(publish):
    init = "from demo._impl import *\n\n__all__ = ['get']\n"
    impl = "__all__ = ['get']\n\ndef get(url{params}): ...\n"
    publish("1.0", {"demo/__init__.py": init, "demo/_impl.py": impl.format(params=", cert=None")})
    publish("2.0", {"demo/__init__.py": init, "demo/_impl.py": impl.format(params="")})

    [change] = apidiff.diff("demo", "1.0", "2.0")

    assert change.public_paths == ("demo.get",)


def test_init_change_is_shown_as_class_call(publish):
    impl = "class Client:\n    def __init__(self, {params}): ...\n\ndef get(url): ...\n"
    publish(
        "1.0",
        {"demo/__init__.py": REEXPORTING_INIT, "demo/_impl.py": impl.format(params="app=None")},
    )
    publish("2.0", {"demo/__init__.py": REEXPORTING_INIT, "demo/_impl.py": impl.format(params="")})

    [change] = apidiff.diff("demo", "1.0", "2.0")

    assert change.definition_path == "demo._impl.Client.__init__"
    assert change.public_paths == ("demo.Client.__init__",)
    assert change.labels() == ["demo.Client(app)"]
    assert change.parameter == Parameter(
        name="app", index=1, kind=ParameterKind.positional_or_keyword
    )


def test_private_top_level_module_falls_back_to_definition_path(publish):
    # Like PyYAML's `_yaml`: the module is private, yet its members are still reported.
    publish("1.0", {"_demo.py": "def get(url, proxies=None): ...\n"})
    publish("2.0", {"_demo.py": "def get(url): ...\n"})

    [change] = apidiff.diff("demo", "1.0", "2.0")

    assert change.public_paths == ("_demo.get",)
    assert change.labels() == ["_demo.get(proxies)"]


def test_parameter_kind_change_keeps_old_and_new_kind(publish):
    publish("1.0", {"demo/__init__.py": "def get(url, timeout=None): ...\n"})
    publish("2.0", {"demo/__init__.py": "def get(url, *, timeout=None): ...\n"})

    [change] = apidiff.diff("demo", "1.0", "2.0")

    assert change.kind == "Parameter kind was changed"
    assert change.parameter == Parameter(
        name="timeout",
        index=1,
        kind=ParameterKind.positional_or_keyword,
        new_kind=ParameterKind.keyword_only,
    )


def test_added_required_parameter_uses_new_signature(publish):
    publish("1.0", {"demo/__init__.py": "def get(url): ...\n"})
    publish("2.0", {"demo/__init__.py": "def get(url, method): ...\n"})

    [change] = apidiff.diff("demo", "1.0", "2.0")

    assert change.kind == "Parameter was added as required"
    assert change.parameter == Parameter(
        name="method", index=1, kind=ParameterKind.positional_or_keyword
    )


def test_removed_module(publish):
    publish("1.0", {"demo/__init__.py": "", "extra.py": "X = 1\n"})
    publish("2.0", {"demo/__init__.py": ""})

    assert apidiff.diff("demo", "1.0", "2.0") == [
        Change(kind="Module was removed", public_paths=("extra",), definition_path="extra")
    ]


def test_version_attribute_is_ignored(publish):
    publish("1.0", {"demo/__init__.py": "__version__ = '1.0'\n"})
    publish("2.0", {"demo/__init__.py": "__version__ = '2.0'\n"})

    assert apidiff.diff("demo", "1.0", "2.0") == []


def test_no_changes(publish):
    source = {"demo/__init__.py": "def get(url): ...\n"}
    publish("1.0", source)
    publish("2.0", source)

    assert apidiff.diff("demo", "1.0", "2.0") == []


def test_changes_are_sorted_by_kind_then_label(publish):
    publish(
        "1.0",
        {"demo/__init__.py": "def b(x): ...\ndef a(x): ...\ndef gone(): ...\n"},
    )
    publish("2.0", {"demo/__init__.py": "def b(): ...\ndef a(): ...\n"})

    changes = apidiff.diff("demo", "1.0", "2.0")

    assert [c.labels() for c in changes] == [["demo.a(x)"], ["demo.b(x)"], ["demo.gone"]]
