from __future__ import annotations

import traceback

import pytest

from safefix.action_parser import ActionParseError, ActionParser
from safefix.domain import RunProcessAction


_CAPTURE_LOCALS_SECRET = "sk-SECRET"


def _rejected_action_with_secret() -> str:
    return '{"type":"sk-SECRET","id":"a1","reason":"reject me","extra":"sk-SECRET"}'


def test_parser_accepts_a_single_valid_run_process_object() -> None:
    action = ActionParser().parse(
        '{"type":"run_process","id":"a1","reason":"test",'
        '"program":"python","args":["-m","pytest"]}'
    )

    assert action == RunProcessAction(
        id="a1",
        reason="test",
        program="python",
        args=("-m", "pytest"),
    )


@pytest.mark.parametrize(
    ("text", "field"),
    [
        (
            '{"type":"finish","id":"a1","reason":"done","summary":"ok"} trailing',
            "$",
        ),
        ('{"type":"unknown","id":"a1","reason":"no"}', "$.type"),
        ('{"type":"run_process","id":"a1","reason":"test"}', "program"),
        ('[{"type":"finish"}]', "$"),
    ],
)
def test_parser_rejects_invalid_or_non_object_actions_with_field_feedback(
    text: str, field: str
) -> None:
    with pytest.raises(ActionParseError) as caught:
        ActionParser().parse(text)

    assert field in caught.value.feedback
    assert str(caught.value) == "model action could not be parsed"


def test_parse_error_does_not_retain_rejected_secret_anywhere() -> None:
    with pytest.raises(ActionParseError) as caught:
        ActionParser().parse(_rejected_action_with_secret())

    error = caught.value
    default_traceback = "".join(traceback.format_exception(error))
    traceback_with_locals = "".join(
        traceback.TracebackException.from_exception(
            error,
            capture_locals=True,
        ).format()
    )
    parser_frames = [
        frame
        for frame, _ in traceback.walk_tb(error.__traceback__)
        if frame.f_globals["__name__"] == "safefix.action_parser"
    ]
    assert _CAPTURE_LOCALS_SECRET not in str(error)
    assert _CAPTURE_LOCALS_SECRET not in error.feedback
    assert _CAPTURE_LOCALS_SECRET not in default_traceback
    assert _CAPTURE_LOCALS_SECRET not in traceback_with_locals
    assert error.__cause__ is None
    assert error.__context__ is None
    assert len(parser_frames) == 1
    assert "text" not in parser_frames[0].f_locals
    assert "payload" not in parser_frames[0].f_locals
