"""Contract tests for the workflow values that a future edit can remove.

The tests read the workflow files as text, so they need no YAML library.
"""

import re
from pathlib import Path

import pytest

REPOSITORY_ROOT = (
    Path(__file__).resolve().parent.parent
)  # The tests run from any directory.
WORKFLOW_DIRECTORY = (
    REPOSITORY_ROOT / ".github" / "workflows"
)  # Each caller workflow is here.
CODEQL_WORKFLOW = (
    WORKFLOW_DIRECTORY / "codeql.yml"
)  # Code scanning keys alerts on this path.
DEVTOOLS_COMMIT = (
    "da02d4c6a2163d1882f2ad25fce80b8ba38304d1"  # The approved v0.6.2 commit.
)
DEVTOOLS_PIN = re.compile(
    r"misthelper-devtools[^\s@]*@([0-9a-f]{40})"
)  # Finds each devtools pin.


def pinned_files() -> list[Path]:
    """Return each file that can pin misthelper-devtools, so no pin escapes the check."""
    workflow_files = sorted(
        WORKFLOW_DIRECTORY.glob("*.yml")
    )  # Each caller workflow can pin it.
    return [
        *workflow_files,
        REPOSITORY_ROOT / "requirements-dev.txt",
    ]  # The gate tools pin it too.


def test_codeql_workflow_calls_shared_analysis() -> None:
    """Make sure the CodeQL workflow calls the pinned shared analysis for Python."""
    text = CODEQL_WORKFLOW.read_text(
        encoding="utf-8"
    )  # The contract applies to this file.
    shared = f"reusable-codeql.yml@{DEVTOOLS_COMMIT} # v0.6.2"  # The approved shared workflow.
    assert shared in text  # A different pin skips the approved analysis.
    assert "languages: '[\"python\"]'" in text  # The repository holds Python code only.
    assert (
        "config-file: ./.github/codeql/codeql-config.yml" in text
    )  # The config names the scan.
    assert (
        REPOSITORY_ROOT / ".github" / "codeql" / "codeql-config.yml"
    ).is_file()  # It must exist.


def test_codeql_workflow_never_cancels_main_run() -> None:
    """Make sure a new commit cannot cancel the analysis of a main commit."""
    text = CODEQL_WORKFLOW.read_text(
        encoding="utf-8"
    )  # The contract applies to this file.
    rule = "cancel-in-progress: ${{ github.ref != 'refs/heads/main' }}"  # Main runs complete.
    assert rule in text  # Without it, a fast second merge drops the first main scan.
    assert "permissions: {}" in text  # The job states its own scopes.
    assert (
        "security-events: write" in text
    )  # The analysis uploads alerts with this scope.


@pytest.mark.parametrize("path", pinned_files(), ids=lambda path: path.name)
def test_every_devtools_pin_names_approved_commit(path: Path) -> None:
    """Make sure each misthelper-devtools pin names the approved release commit."""
    pins = DEVTOOLS_PIN.findall(
        path.read_text(encoding="utf-8")
    )  # Each pin in the file.
    assert all(
        pin == DEVTOOLS_COMMIT for pin in pins
    ), pins  # One release for each caller.


def test_devtools_pin_check_examines_pins() -> None:
    """Make sure the pin test finds pins, so it can fail when a pin drifts."""
    pins = [
        pin
        for path in pinned_files()
        for pin in DEVTOOLS_PIN.findall(path.read_text("utf-8"))
    ]
    assert len(pins) >= 7, pins  # Six workflow pins and one requirements pin exist.
