from __future__ import annotations

import json
import traceback
from types import TracebackType
from typing import NoReturn

import pytest

import safefix.action_parser as action_parser_module
from safefix.action_parser import ActionParseError, ActionParser
from safefix.domain import RunProcessAction


_CAPTURE_LOCALS_SECRET = "sk-SECRET"
_DEEP_DECODER_CANARY = "DEEP_DECODER_CANARY_123"
_INTERNAL_FAILURE_CANARY = "INTERNAL_RUNTIME_CANARY_123"
_INTERNAL_INPUT_CANARY = "MODEL_INPUT_CANARY_123"
_FEEDBACK_INPUT_CANARY = "FEEDBACK_INPUT_CANARY_123"
_ADAPTER_RECURSION_CANARY = "ADAPTER_RECURSION_CANARY_123"
_FORMATTER_FAILURE_CANARY = "FORMATTER_RUNTIME_CANARY_123"
_INTERRUPT_INPUT_CANARY = "INTERRUPT_MODEL_CANARY_123"


def _rejected_action_with_secret() -> str:
    return '{"type":"sk-SECRET","id":"a1","reason":"reject me","extra":"sk-SECRET"}'


def _deep_rejected_action() -> str:
    depth = 5_000
    return '{"x":' * depth + f'"{_DEEP_DECODER_CANARY}"' + "}" * depth


def _internal_failure_action() -> str:
    return (
        '{"type":"finish","id":"a1",'
        f'"reason":"{_INTERNAL_INPUT_CANARY}","summary":"ok"}}'
    )


def _validation_failure_action() -> str:
    return (
        '{"type":"run_process","id":"a1",'
        f'"reason":"{_INTERNAL_INPUT_CANARY}","args":[]}}'
    )


def _many_invalid_args_action() -> str:
    invalid_args = [{_FEEDBACK_INPUT_CANARY: "secret"}, *range(4_999)]
    return json.dumps(
        {
            "type": "run_process",
            "id": "a1",
            "reason": "test",
            "program": "python",
            "args": invalid_args,
        },
        separators=(",", ":"),
    )


def _huge_integer_action() -> str:
    return '{"type":"list_files","id":"a1","reason":"test","limit":' + "9" * 5_000 + "}"


def _parser_traceback(error: BaseException) -> TracebackType:
    current = error.__traceback__
    while current is not None:
        if current.tb_frame.f_globals["__name__"] == "safefix.action_parser":
            return current
        current = current.tb_next
    raise AssertionError("parser traceback frame missing")


class _BrokenActionAdapter:
    def validate_python(self, payload: object) -> NoReturn:
        del payload
        raise RuntimeError(_INTERNAL_FAILURE_CANARY)


class _RecursiveActionAdapter:
    def validate_python(self, payload: object) -> NoReturn:
        del payload
        raise RecursionError(_ADAPTER_RECURSION_CANARY)


class _InterruptingActionAdapter:
    def validate_python(self, payload: object) -> NoReturn:
        del payload
        raise KeyboardInterrupt()


def _broken_validation_feedback(error: object) -> NoReturn:
    del error
    raise RuntimeError(_FORMATTER_FAILURE_CANARY)


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
    parser_traceback = _parser_traceback(error)
    parser_traceback_without_locals = "".join(
        traceback.TracebackException(
            type(error),
            error,
            parser_traceback,
            capture_locals=False,
        ).format()
    )
    parser_traceback_with_locals = "".join(
        traceback.TracebackException(
            type(error),
            error,
            parser_traceback,
            capture_locals=True,
        ).format()
    )
    assert _CAPTURE_LOCALS_SECRET not in str(error)
    assert _CAPTURE_LOCALS_SECRET not in error.feedback
    assert _CAPTURE_LOCALS_SECRET not in parser_traceback_without_locals
    assert _CAPTURE_LOCALS_SECRET not in parser_traceback_with_locals
    assert error.__cause__ is None
    assert error.__context__ is None
    assert "text" not in parser_traceback.tb_frame.f_locals
    assert "payload" not in parser_traceback.tb_frame.f_locals


def test_deep_decoder_error_is_sanitized_without_retaining_input() -> None:
    with pytest.raises(ActionParseError) as caught:
        ActionParser().parse(_deep_rejected_action())

    error = caught.value
    parser_traceback = _parser_traceback(error)
    parser_traceback_without_locals = "".join(
        traceback.TracebackException(
            type(error),
            error,
            parser_traceback,
            capture_locals=False,
        ).format()
    )
    parser_traceback_with_locals = "".join(
        traceback.TracebackException(
            type(error),
            error,
            parser_traceback,
            capture_locals=True,
        ).format()
    )
    assert str(error) == "model action could not be parsed"
    assert _DEEP_DECODER_CANARY not in error.feedback
    assert error.feedback.startswith("INVALID_ACTION: ")
    assert _DEEP_DECODER_CANARY not in parser_traceback_without_locals
    assert _DEEP_DECODER_CANARY not in parser_traceback_with_locals
    assert error.__cause__ is None
    assert error.__context__ is None
    assert {"text", "payload", "exc"}.isdisjoint(parser_traceback.tb_frame.f_locals)


def test_internal_adapter_failure_is_distinct_and_sanitized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        action_parser_module,
        "ACTION_ADAPTER",
        _BrokenActionAdapter(),
    )

    with pytest.raises(Exception) as caught:
        ActionParser().parse(_internal_failure_action())

    error = caught.value
    parser_traceback = _parser_traceback(error)
    parser_traceback_with_locals = "".join(
        traceback.TracebackException(
            type(error),
            error,
            parser_traceback,
            capture_locals=True,
        ).format()
    )
    public_error = f"{error!r} {error} {error.__dict__!r}"
    assert not isinstance(error, ActionParseError)
    assert type(error).__name__ == "ActionParserInternalError"
    assert str(error) == "action parser internal failure"
    assert _INTERNAL_INPUT_CANARY not in public_error
    assert _INTERNAL_FAILURE_CANARY not in public_error
    assert _INTERNAL_INPUT_CANARY not in parser_traceback_with_locals
    assert _INTERNAL_FAILURE_CANARY not in parser_traceback_with_locals
    assert error.__cause__ is None
    assert error.__context__ is None
    assert {"text", "payload", "exc"}.isdisjoint(parser_traceback.tb_frame.f_locals)


def test_decoder_huge_integer_is_a_safe_input_error() -> None:
    with pytest.raises(ActionParseError) as caught:
        ActionParser().parse(_huge_integer_action())

    assert str(caught.value) == "model action could not be parsed"
    assert caught.value.feedback == "INVALID_ACTION: $: invalid JSON"
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def test_adapter_recursion_is_a_sanitized_internal_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        action_parser_module,
        "ACTION_ADAPTER",
        _RecursiveActionAdapter(),
    )

    with pytest.raises(Exception) as caught:
        ActionParser().parse(_internal_failure_action())

    error = caught.value
    parser_traceback = _parser_traceback(error)
    parser_traceback_with_locals = "".join(
        traceback.TracebackException(
            type(error), error, parser_traceback, capture_locals=True
        ).format()
    )
    assert type(error).__name__ == "ActionParserInternalError"
    assert str(error) == "action parser internal failure"
    assert _ADAPTER_RECURSION_CANARY not in parser_traceback_with_locals
    assert _INTERNAL_INPUT_CANARY not in parser_traceback_with_locals
    assert error.__cause__ is None
    assert error.__context__ is None


def test_feedback_formatter_failure_is_a_sanitized_internal_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        action_parser_module,
        "_validation_feedback",
        _broken_validation_feedback,
    )

    with pytest.raises(Exception) as caught:
        ActionParser().parse(_validation_failure_action())

    error = caught.value
    parser_traceback = _parser_traceback(error)
    parser_traceback_with_locals = "".join(
        traceback.TracebackException(
            type(error), error, parser_traceback, capture_locals=True
        ).format()
    )
    parser_frame_locals = [
        frame.f_locals
        for frame, _ in traceback.walk_tb(error.__traceback__)
        if frame.f_globals["__name__"] == "safefix.action_parser"
    ]
    assert type(error).__name__ == "ActionParserInternalError"
    assert str(error) == "action parser internal failure"
    assert _FORMATTER_FAILURE_CANARY not in parser_traceback_with_locals
    assert _INTERNAL_INPUT_CANARY not in parser_traceback_with_locals
    assert error.__cause__ is None
    assert error.__context__ is None
    assert all(
        {"text", "payload", "exc"}.isdisjoint(frame_locals)
        for frame_locals in parser_frame_locals
    )


def test_adapter_keyboard_interrupt_propagates_without_sensitive_locals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        action_parser_module,
        "ACTION_ADAPTER",
        _InterruptingActionAdapter(),
    )
    text = (
        '{"type":"finish","id":"a1",'
        f'"reason":"{_INTERRUPT_INPUT_CANARY}","summary":"ok"}}'
    )

    with pytest.raises(KeyboardInterrupt) as caught:
        ActionParser().parse(text)

    error = caught.value
    parser_traceback = _parser_traceback(error)
    parser_traceback_with_locals = "".join(
        traceback.TracebackException(
            type(error), error, parser_traceback, capture_locals=True
        ).format()
    )
    parser_frame_locals = [
        frame.f_locals
        for frame, _ in traceback.walk_tb(error.__traceback__)
        if frame.f_globals["__name__"] == "safefix.action_parser"
    ]
    assert type(error) is KeyboardInterrupt
    assert _INTERRUPT_INPUT_CANARY not in parser_traceback_with_locals
    assert all(
        {"text", "payload", "exc"}.isdisjoint(frame_locals)
        for frame_locals in parser_frame_locals
    )


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


def test_validation_feedback_is_limited_to_eight_items() -> None:
    with pytest.raises(ActionParseError) as caught:
        ActionParser().parse(_many_invalid_args_action())

    feedback = caught.value.feedback
    assert feedback.startswith("INVALID_ACTION: ")
    assert feedback.count(": invalid value") == 8
    assert "$.run_process.args[0]: invalid value" in feedback
    assert "$.run_process.args[7]: invalid value" in feedback
    assert "$.run_process.args[8]: invalid value" not in feedback
    assert "TRUNCATED: 4992 additional errors omitted" in feedback
    assert len(feedback) <= 512
    assert _FEEDBACK_INPUT_CANARY not in feedback
