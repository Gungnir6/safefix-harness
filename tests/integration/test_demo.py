from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from safefix.demo import SCENARIOS, run_scenario


def test_all_demo_prints_three_passed_scenarios() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "safefix.demo", "all"],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout.splitlines() == [
        "guardrail: PASS",
        "feedback: PASS",
        "approval: PASS",
    ]
    assert result.stderr == ""


@pytest.mark.parametrize("name", ("guardrail", "feedback", "approval"))
def test_each_scenario_is_deterministic_and_cleans_temporary_workspace(
    name: str,
) -> None:
    first = run_scenario(name)
    second = run_scenario(name)

    assert first.passed is True
    assert first.events == second.events
    assert first.workspace_removed is True
    assert first.events


def test_scenarios_expose_the_three_required_mechanisms() -> None:
    assert tuple(SCENARIOS) == ("guardrail", "feedback", "approval")
    assert run_scenario("guardrail").events == (
        "POLICY:DENY",
        "RULE:CMD_PRIVILEGE_ESCALATION",
        "TOOL_CALLS:0",
    )
    assert run_scenario("feedback").events == (
        "VALIDATION:FAIL",
        "PATCH:WRONG",
        "VALIDATION:FAIL",
        "PATCH:CORRECT",
        "VALIDATION:PASS",
    )
    assert run_scenario("approval").events == (
        "APPROVAL:PENDING",
        "STORE:REOPENED",
        "ACTION_MISMATCH:BLOCKED",
        "APPROVAL:APPROVED",
        "TOKEN_REPLAY:BLOCKED",
        "TOOL_CALLS:1",
    )


def test_embedded_fixture_is_never_modified() -> None:
    fixture = Path(__file__).parents[2] / "examples" / "python_bug" / "calculator.py"
    before = fixture.read_bytes()

    run_scenario("feedback")

    assert fixture.read_bytes() == before
