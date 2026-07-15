from __future__ import annotations

import traceback

import pytest

from safefix.action_parser import ActionParseError, ActionParser
from safefix.domain import RunProcessAction


_CAPTURE_LOCALS_SECRET = "sk-SECRET"
_DEEP_DECODER_CANARY = "DEEP_DECODER_CANARY_123"


def _rejected_action_with_secret() -> str:
    return '{"type":"sk-SECRET","id":"a1","reason":"reject me","extra":"sk-SECRET"}'


def _deep_rejected_action() -> str:
    depth = 5_000
    return '{"x":' * depth + f'"{_DEEP_DECODER_CANARY}"' + "}" * depth


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
        ("{", "$"),
    ],
)
def test_parser_rejects_invalid_or_non_object_actions_with_field_feedback(
    text: str, field: str
) -> None:
    with pytest.raises(ActionParseError) as caught:
        ActionParser().parse(text)

    assert field in caught.value.feedback
    assert caught.value.feedback.startswith("INVALID_ACTION: ")
    assert str(caught.value) == "model action could not be parsed"


def test_parse_error_and_parser_frame_do_not_retain_rejected_secret() -> None:
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


def test_deep_decoder_error_is_sanitized_without_retaining_input() -> None:
    with pytest.raises(ActionParseError) as caught:
        ActionParser().parse(_deep_rejected_action())

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
    assert str(error) == "model action could not be parsed"
    assert _DEEP_DECODER_CANARY not in error.feedback
    assert error.feedback.startswith("INVALID_ACTION: ")
    assert _DEEP_DECODER_CANARY not in default_traceback
    assert _DEEP_DECODER_CANARY not in traceback_with_locals
    assert error.__cause__ is None
    assert error.__context__ is None
    assert len(parser_frames) == 1
    assert {"text", "payload", "exc"}.isdisjoint(parser_frames[0].f_locals)


@pytest.mark.parametrize("unknown_field", ["CANARY_123", "秘密字段"])
def test_unknown_extra_field_names_are_redacted_from_feedback(
    unknown_field: str,
) -> None:
    text = (
        '{"type":"finish","id":"a1","reason":"done","summary":"ok",'
        f'"{unknown_field}":"rejected"}}'
    )

    with pytest.raises(ActionParseError) as caught:
        ActionParser().parse(text)

    assert unknown_field not in caught.value.feedback
    assert caught.value.feedback.startswith("INVALID_ACTION: ")
    assert "$.finish.?: unexpected field" in caught.value.feedback


@pytest.mark.parametrize(
    "text",
    [
        (
            '{"type":"run_process","id":"a1","reason":"test",'
            '"program":"blocked","program":"python"}'
        ),
        (
            '{"type":"finish","id":"a1","reason":"done","summary":"ok",'
            '"metadata":{"CANARY_DUP_KEY":"CANARY_FIRST",'
            '"CANARY_DUP_KEY":"CANARY_SECOND"}}'
        ),
    ],
)
def test_duplicate_object_keys_at_any_depth_are_rejected(text: str) -> None:
    with pytest.raises(ActionParseError) as caught:
        ActionParser().parse(text)

    assert caught.value.feedback == "INVALID_ACTION: $: invalid JSON"
    assert "CANARY" not in caught.value.feedback


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_nonstandard_json_constants_cannot_be_overridden_by_a_duplicate_key(
    constant: str,
) -> None:
    text = (
        '{"type":"run_process","id":"a1","reason":"test",'
        f'"program":"python","args":{constant},"args":[]}}'
    )

    with pytest.raises(ActionParseError) as caught:
        ActionParser().parse(text)

    assert caught.value.feedback == "INVALID_ACTION: $: invalid JSON"
    assert constant not in caught.value.feedback
