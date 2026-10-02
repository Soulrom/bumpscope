from pathlib import Path

import pytest
from griffe import BreakageKind, ParameterKind

from bumpscope.apidiff import MODULE_REMOVED, Change, Parameter
from bumpscope.impact import find_impacts
from bumpscope.usages import Usage

POSITIONAL_OR_KEYWORD = ParameterKind.positional_or_keyword
FILE = Path("app.py")


def _change(kind, parameter=None, definition="demo._impl.get", public=("demo.get",)):
    return Change(
        kind=kind.value if isinstance(kind, BreakageKind) else kind,
        public_paths=public,
        definition_path=definition,
        parameter=parameter,
    )


def _param(name="proxies", index=1, kind=POSITIONAL_OR_KEYWORD, new_kind=None):
    return Parameter(name=name, index=index, kind=kind, new_kind=new_kind)


def _call(path="demo.get", positional=1, keywords=(), **unpacking):
    return Usage(path, FILE, 3, "call", positional, frozenset(keywords), **unpacking)


def _labels(change, usage):
    return [impact.label for impact in find_impacts([change], [usage])]


# Any reference: removed object, changed kind, removed base class, removed module.


@pytest.mark.parametrize(
    "kind",
    [
        BreakageKind.OBJECT_REMOVED,
        BreakageKind.OBJECT_CHANGED_KIND,
        BreakageKind.CLASS_REMOVED_BASE,
    ],
)
def test_any_reference_kinds_match_imports(kind):
    assert _labels(_change(kind), Usage("demo.get", FILE, 1)) == ["demo.get"]


def test_reference_to_a_member_of_a_removed_object_matches():
    change = _change(BreakageKind.OBJECT_REMOVED, definition="demo.Client", public=("demo.Client",))

    assert _labels(change, Usage("demo.Client.send", FILE, 1)) == ["demo.Client"]


def test_removed_module_matches_its_import():
    change = _change(MODULE_REMOVED, definition="extra", public=("extra",))

    assert _labels(change, Usage("extra", FILE, 1)) == ["extra"]


def test_similar_prefix_does_not_match():
    change = _change(BreakageKind.OBJECT_REMOVED)

    assert _labels(change, Usage("demo.get_all", FILE, 1)) == []


# Parameter removed: the call passes it.


@pytest.mark.parametrize(
    ("usage", "expected"),
    [
        (_call(positional=1, keywords={"proxies"}), ["demo.get(proxies)"]),
        (_call(positional=2), ["demo.get(proxies)"]),
        (_call(positional=1), []),
        (_call(positional=1, unpacks_args=True), ["demo.get(proxies)"]),
        (_call(positional=1, unpacks_kwargs=True), ["demo.get(proxies)"]),
        (Usage("demo.get", FILE, 1), []),
    ],
    ids=["keyword", "positional", "not-passed", "star-args", "star-kwargs", "import-only"],
)
def test_parameter_removed(usage, expected):
    change = _change(BreakageKind.PARAMETER_REMOVED, _param())

    assert _labels(change, usage) == expected


def test_keyword_only_parameter_is_not_passed_positionally():
    change = _change(BreakageKind.PARAMETER_REMOVED, _param(kind=ParameterKind.keyword_only))

    assert _labels(change, _call(positional=5)) == []


def test_init_change_matches_class_call_without_self():
    # `__init__(self, app)`: `Client(x)` passes `app` positionally, as the first argument.
    change = _change(
        BreakageKind.PARAMETER_REMOVED,
        _param(name="app", index=1),
        definition="demo._client.Client.__init__",
        public=("demo.Client.__init__",),
    )

    assert _labels(change, _call("demo.Client", positional=1)) == ["demo.Client(app)"]
    assert _labels(change, _call("demo.Client", positional=0)) == []


def test_private_import_matches_through_the_definition_path():
    change = _change(BreakageKind.PARAMETER_REMOVED, _param())

    usage = _call("demo._impl.get", keywords={"proxies"})

    assert _labels(change, usage) == ["demo._impl.get(proxies)"]


# Parameter moved: the call passes it positionally.


@pytest.mark.parametrize(
    ("usage", "expected"),
    [
        (_call(positional=2), ["demo.get(proxies)"]),
        (_call(positional=1, keywords={"proxies"}), []),
        (_call(positional=0, unpacks_args=True), ["demo.get(proxies)"]),
    ],
    ids=["positional", "keyword", "star-args"],
)
def test_parameter_moved(usage, expected):
    change = _change(BreakageKind.PARAMETER_MOVED, _param())

    assert _labels(change, usage) == expected


# Parameter kind changed: the call passes it in a way the new kind forbids.


@pytest.mark.parametrize(
    ("new_kind", "usage", "expected"),
    [
        (ParameterKind.keyword_only, _call(positional=2), ["demo.get(proxies)"]),
        (ParameterKind.keyword_only, _call(positional=1, keywords={"proxies"}), []),
        (ParameterKind.positional_only, _call(keywords={"proxies"}), ["demo.get(proxies)"]),
        (ParameterKind.positional_only, _call(positional=2), []),
    ],
    ids=["to-keyword-only-positional", "to-keyword-only-keyword", "to-pos-only-kw", "to-pos-only"],
)
def test_parameter_changed_kind(new_kind, usage, expected):
    change = _change(BreakageKind.PARAMETER_CHANGED_KIND, _param(new_kind=new_kind))

    assert _labels(change, usage) == expected


# Required parameter added, or now required: the call does not pass it.


@pytest.mark.parametrize(
    "kind", [BreakageKind.PARAMETER_ADDED_REQUIRED, BreakageKind.PARAMETER_CHANGED_REQUIRED]
)
@pytest.mark.parametrize(
    ("usage", "expected"),
    [
        (_call(positional=1), ["demo.get(proxies)"]),
        (_call(positional=2), []),
        (_call(positional=1, keywords={"proxies"}), []),
        (_call(positional=1, unpacks_kwargs=True), ["demo.get(proxies)"]),
        (_call(positional=2, unpacks_kwargs=True), []),
    ],
    ids=["not-passed", "positional", "keyword", "star-kwargs", "passed-and-star-kwargs"],
)
def test_parameter_now_required(kind, usage, expected):
    change = _change(kind, _param())

    assert _labels(change, usage) == expected


# Skipped kinds never match.


@pytest.mark.parametrize(
    "kind",
    [
        BreakageKind.PARAMETER_CHANGED_DEFAULT,
        BreakageKind.RETURN_CHANGED_TYPE,
        BreakageKind.ATTRIBUTE_CHANGED_TYPE,
        BreakageKind.ATTRIBUTE_CHANGED_VALUE,
    ],
)
def test_skipped_kinds(kind):
    change = _change(kind, _param() if kind.name.startswith("PARAMETER") else None)

    assert _labels(change, _call(positional=3, keywords={"proxies"})) == []


def test_impacts_are_sorted_by_kind_then_location():
    removed = _change(BreakageKind.PARAMETER_REMOVED, _param())
    gone = _change(BreakageKind.OBJECT_REMOVED, definition="demo.old", public=("demo.old",))
    usages = [
        Usage("demo.old", Path("b.py"), 9),
        _call(keywords={"proxies"}),
        Usage("demo.old", Path("a.py"), 4),
    ]

    impacts = find_impacts([removed, gone], usages)

    assert [(i.change.kind, i.usage.file.as_posix(), i.usage.line) for i in impacts] == [
        ("Parameter was removed", "app.py", 3),
        ("Public object was removed", "a.py", 4),
        ("Public object was removed", "b.py", 9),
    ]
