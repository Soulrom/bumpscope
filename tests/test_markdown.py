"""Markdown report: layout (design decision 15) and size cap (decision 16)."""

import re
from pathlib import Path

from bumpscope.apidiff import Change
from bumpscope.impact import Impact
from bumpscope.report import PackageResult, Report, render_markdown
from bumpscope.usages import Usage

REMOVED = "Parameter was removed"


def _impact(file: str, line: int, label: str, kind: str = REMOVED) -> Impact:
    change = Change(kind=kind, public_paths=(label,), definition_path=label)
    return Impact(change, Usage(label, Path(file), line), label)


HTTPX = PackageResult(
    "httpx", "0.24.1", "0.28.0", impacts=(_impact("app/client.py", 5, "httpx.Client(proxies)"),)
)
SDK = PackageResult("internal-sdk", "2.1.0", reason="not installed from PyPI")
RICH = PackageResult("rich", "13.7.0", "15.0.0")
CLICK = PackageResult("click", "8.1.7", "8.3.0")
UP_TO_DATE = tuple(PackageResult(f"ok{i}", "1", "1") for i in range(3))


def test_layout_matches_the_design():
    report = Report((HTTPX, SDK, RICH, CLICK, *UP_TO_DATE))

    assert render_markdown(report) == (
        "## bumpscope: 1 of 4 updates affects your code\n"
        "\n"
        "### Affects your code\n"
        "\n"
        "#### httpx 0.24.1 → 0.28.0\n"
        "\n"
        "| Location | Change | Kind |\n"
        "|---|---|---|\n"
        "| `app/client.py:5` | `httpx.Client(proxies)` | Parameter was removed |\n"
        "\n"
        "### Could not check\n"
        "\n"
        "- `internal-sdk` 2.1.0: not installed from PyPI\n"
        "\n"
        "<details><summary>No impact found (2)</summary>\n"
        "\n"
        "click 8.1.7 → 8.3.0, rich 13.7.0 → 15.0.0\n"
        "\n"
        "</details>\n"
        "\n"
        "3 packages up to date. Method calls on instances are not analyzed.\n"
    )


def test_title_without_impacts_never_says_safe():
    nothing = render_markdown(Report((RICH,)))
    unchecked = render_markdown(Report((RICH, SDK)))
    empty = render_markdown(Report(()))

    assert nothing.startswith("## bumpscope: no impact found in 1 update\n")
    assert unchecked.startswith(
        "## bumpscope: no impact found in 1 update, 1 could not be checked\n"
    )
    assert empty.startswith("## bumpscope: no updates available\n")
    assert all("safe" not in text.lower() for text in (nothing, unchecked, empty))


def test_title_plural():
    other = PackageResult("demo", "1", "2", impacts=(_impact("a.py", 1, "demo.x"),))

    assert render_markdown(Report((HTTPX, other))).startswith(
        "## bumpscope: 2 of 2 updates affect your code\n"
    )


def test_empty_sections_are_left_out_and_the_note_stays():
    text = render_markdown(Report((RICH,)))

    assert "Affects your code" not in text
    assert "Could not check" not in text
    assert "up to date" not in text
    assert text.endswith("\nMethod calls on instances are not analyzed.\n")


def test_unchecked_versions():
    with_target = PackageResult("demo", "1.0", "2.0", reason="no wheel")
    without_version = PackageResult("ghost", None, reason="not found on PyPI")

    text = render_markdown(Report((with_target, without_version)))

    assert "- `demo` 1.0 → 2.0: no wheel\n" in text
    assert "- `ghost`: not found on PyPI\n" in text


def test_pipes_in_cells_are_escaped():
    result = PackageResult("demo", "1", "2", impacts=(_impact("a|b.py", 3, "demo.f(x|y)"),))

    text = render_markdown(Report((result,)))

    assert "| `a\\|b.py:3` | `demo.f(x\\|y)` | Parameter was removed |\n" in text


def test_impacts_are_grouped_by_package_in_one_table_each():
    result = PackageResult(
        "demo",
        "1",
        "2",
        impacts=(
            _impact("a.py", 1, "demo.f(x)"),
            _impact("b.py", 2, "demo.gone", kind="Public object was removed"),
        ),
    )

    text = render_markdown(Report((result,)))

    assert text.count("| Location | Change | Kind |") == 1
    assert "| `b.py:2` | `demo.gone` | Public object was removed |" in text


# Size cap


def _many_no_impact(n: int) -> tuple[PackageResult, ...]:
    return tuple(PackageResult(f"package{i:04}", "1.0", "2.0") for i in range(n))


def _many_impacts(n: int) -> PackageResult:
    return PackageResult(
        "big", "1", "2", impacts=tuple(_impact(f"f{i:04}.py", i, f"big.f{i}(x)") for i in range(n))
    )


def _assert_well_formed(text: str) -> None:
    assert text.count("<details>") == text.count("</details>")
    for line in text.splitlines():
        if line == "|---|---|---|":
            continue
        if line.startswith("|"):
            # Three cells: a cut in the middle of a row would leave fewer separators.
            assert line.endswith(" |") and line.count(" | ") == 2, line


def test_small_report_is_not_cut():
    report = Report((HTTPX, *_many_no_impact(20)))

    assert "more" not in render_markdown(report, limit=60_000)


def test_no_impact_found_is_trimmed_first():
    report = Report((HTTPX, SDK, *_many_no_impact(500)))

    text = render_markdown(report, limit=3_000)

    assert len(text) <= 3_000
    assert "| `app/client.py:5` |" in text
    assert "- `internal-sdk` 2.1.0: not installed from PyPI" in text
    assert re.search(r"package\d{4} 1\.0 → 2\.0, and \d+ more\n", text)
    assert "<summary>No impact found (500)</summary>" in text
    _assert_well_formed(text)


def test_impact_rows_are_trimmed_from_the_end_after_no_impact_is_gone():
    report = Report((_many_impacts(400), SDK, *_many_no_impact(50)))

    text = render_markdown(report, limit=4_000)

    assert len(text) <= 4_000
    assert "| `f0000.py:0` |" in text
    assert "| `f0399.py:399` |" not in text
    hidden = int(re.search(r"\.\.\.and (\d+) more impacts\.", text).group(1))
    shown = text.count("| `f")
    assert shown + hidden == 400
    assert "- `internal-sdk` 2.1.0: not installed from PyPI" in text
    assert "<summary>No impact found (50)</summary>" in text
    _assert_well_formed(text)


def test_title_and_could_not_check_are_never_cut():
    unchecked = tuple(
        PackageResult(f"sdk{i}", "1", reason="not installed from PyPI") for i in range(80)
    )
    report = Report((_many_impacts(50), *unchecked))

    text = render_markdown(report, limit=1_000)

    assert text.startswith("## bumpscope: 1 of 81 updates affects your code\n")
    assert all(f"- `sdk{i}` 1: not installed from PyPI" in text for i in range(80))
    _assert_well_formed(text)


def test_reasons_are_one_line_and_cannot_inject_markdown():
    reason = "HTTPError: <Response [404]> in pkg.__init__\n# not a heading | *args"
    result = PackageResult("demo", "1.0", reason=reason)

    text = render_markdown(Report((result,)))

    line = next(line for line in text.splitlines() if line.startswith("- `demo`"))
    assert line == (
        r"- `demo` 1.0: HTTPError: \<Response \[404\]\> in pkg.\_\_init\_\_ \# not a heading "
        r"\| \*args"
    )
    assert "\n# not a heading" not in text


def test_title_counts_only_checked_packages_when_nothing_is_affected():
    only_unchecked = render_markdown(Report((SDK,)))
    mixed = render_markdown(Report((RICH, SDK)))

    assert only_unchecked.startswith("## bumpscope: 1 package could not be checked\n")
    assert mixed.startswith("## bumpscope: no impact found in 1 update, 1 could not be checked\n")


def test_unknown_installed_version_with_a_known_target():
    result = PackageResult("x", None, "2.0", reason="not found")

    text = render_markdown(Report((result,)))

    assert "- `x` → 2.0: not found\n" in text
