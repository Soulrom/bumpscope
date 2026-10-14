import io
from pathlib import Path

import pytest
from rich.console import Console

from bumpscope.apidiff import Change, Parameter
from bumpscope.impact import Impact
from bumpscope.report import EXIT_ERROR, EXIT_FOUND, EXIT_OK, PackageResult, Report, render_text
from bumpscope.usages import Usage

REMOVED = Change(
    kind="Parameter was removed",
    public_paths=("demo.get",),
    definition_path="demo.get",
    parameter=Parameter(name="proxies", index=1, kind=None),
)
GONE = Change(
    kind="Public object was removed", public_paths=("demo.gone",), definition_path="demo.gone"
)


def _impact(change: Change, file: str, line: int, label: str) -> Impact:
    return Impact(change, Usage(label.split("(")[0], Path(file), line), label)


AFFECTED = PackageResult(
    "demo",
    "1.0",
    "2.0",
    impacts=(
        _impact(REMOVED, "app/client.py", 3, "demo.get(proxies)"),
        _impact(GONE, "app/old.py", 1, "demo.gone"),
    ),
)
NO_IMPACT = PackageResult("rich", "13.7.0", "15.0.0")
UNCHECKED = PackageResult("internal-sdk", "2.1.0", reason="not installed from PyPI")
UP_TO_DATE = PackageResult("click", "8.3.0", "8.3.0")


def _render(report: Report) -> str:
    stream = io.StringIO()
    render_text(report, Console(file=stream, width=200, color_system=None))
    return stream.getvalue()


def test_full_report_follows_the_section_order():
    report = Report((NO_IMPACT, UP_TO_DATE, UNCHECKED, AFFECTED))

    assert _render(report) == (
        "3 updates available\n"
        "\n"
        "AFFECTS YOUR CODE (1)\n"
        "  demo 1.0 -> 2.0\n"
        "    PARAMETER WAS REMOVED\n"
        "      app/client.py:3  demo.get(proxies)\n"
        "    PUBLIC OBJECT WAS REMOVED\n"
        "      app/old.py:1  demo.gone\n"
        "\n"
        "COULD NOT CHECK (1)\n"
        "  internal-sdk 2.1.0: not installed from PyPI\n"
        "\n"
        "NO IMPACT FOUND (1)\n"
        "  rich 13.7.0 -> 15.0.0\n"
        "\n"
        "1 package up to date\n"
        "(method calls on instances are not analyzed)\n"
    )


def test_empty_sections_are_left_out_and_the_note_stays():
    output = _render(Report((NO_IMPACT,)))

    assert "AFFECTS YOUR CODE" not in output
    assert "COULD NOT CHECK" not in output
    assert "up to date" not in output
    assert output.endswith("(method calls on instances are not analyzed)\n")
    assert "safe" not in output.lower()


def test_packages_are_listed_by_name_and_counts_are_pluralized():
    report = Report(
        (
            PackageResult("Zeta", "1", "2"),
            PackageResult("alpha", "1", "2"),
            PackageResult("a", "1", "1"),
            PackageResult("b", "1", "1"),
        )
    )

    output = _render(report)

    assert "2 updates available\n" in output
    assert "NO IMPACT FOUND (2)\n  alpha 1 -> 2, Zeta 1 -> 2\n" in output
    assert "2 packages up to date\n" in output


def test_unchecked_package_without_known_version():
    output = _render(Report((PackageResult("demo", None, reason="not found on PyPI"),)))

    assert "COULD NOT CHECK (1)\n  demo: not found on PyPI\n" in output


def test_markup_in_names_is_printed_literally():
    change = Change(kind="Public object was removed", public_paths=("x",), definition_path="x")
    result = PackageResult("demo", "1", "2", impacts=(_impact(change, "[red]a.py", 1, "x[bold]"),))

    output = _render(Report((result,)))

    assert "[red]a.py:1  x[bold]" in output


@pytest.mark.parametrize(
    ("results", "expected"),
    [
        ((AFFECTED, UNCHECKED, NO_IMPACT), EXIT_FOUND),
        ((UNCHECKED, NO_IMPACT, UP_TO_DATE), EXIT_ERROR),
        ((NO_IMPACT, UP_TO_DATE), EXIT_OK),
        ((), EXIT_OK),
    ],
    ids=["impact-wins", "unchecked-never-ok", "nothing-found", "empty"],
)
def test_exit_code(results, expected):
    assert Report(results).exit_code() == expected


def test_result_outcomes_are_exclusive():
    assert AFFECTED.is_affected and not AFFECTED.is_unchecked and not AFFECTED.is_up_to_date
    assert UNCHECKED.is_unchecked and not UNCHECKED.is_up_to_date
    assert UP_TO_DATE.is_up_to_date and not UP_TO_DATE.is_affected
    assert not NO_IMPACT.is_affected and not NO_IMPACT.is_unchecked


@pytest.mark.parametrize(
    "kwargs",
    [
        {"from_version": "1", "to_version": "2", "reason": "x", "impacts": AFFECTED.impacts},
        {"from_version": "1", "to_version": None},
        {"from_version": None, "to_version": None},
        {"from_version": "1", "to_version": "1.0", "impacts": AFFECTED.impacts},
    ],
    ids=["reason-and-impacts", "no-to-version", "no-versions", "impacts-but-up-to-date"],
)
def test_result_with_more_than_one_outcome_is_rejected(kwargs):
    with pytest.raises(ValueError):
        PackageResult("demo", **kwargs)


def test_equal_versions_spelled_differently_are_up_to_date():
    assert PackageResult("demo", "2.0", "2.0.0").is_up_to_date


def test_unchecked_package_shows_the_target_version_when_known():
    output = _render(Report((PackageResult("demo", "1.0", "2.0", reason="no wheel"),)))

    assert "  demo 1.0 -> 2.0: no wheel\n" in output


def test_affected_packages_are_separated_by_a_blank_line():
    other = PackageResult("zeta", "1", "2", impacts=(_impact(GONE, "b.py", 2, "zeta.x"),))

    output = _render(Report((other, AFFECTED)))

    assert "      app/old.py:1  demo.gone\n\n  zeta 1 -> 2\n" in output
    assert "      b.py:2  zeta.x\n\nCOULD NOT CHECK" not in output
    assert output.count("\n\n\n") == 0
