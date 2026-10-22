"""cognition/decision.py 的解析、校验与钳制测试。"""

from __future__ import annotations

import pytest

from manager.cognition.decision import DecisionParser, repair_message
from manager.shared.types import DecisionError


class _StubConfig:
    next_wake_max_seconds = 3600


@pytest.fixture()
def parser() -> DecisionParser:
    return DecisionParser(_StubConfig())


# ---------------------------------------------------------------- 解析


def test_parse_plain_json(parser: DecisionParser) -> None:
    decision = parser.parse('{"action": "idle"}')
    assert decision.action.value == "idle"


def test_parse_json_in_code_block(parser: DecisionParser) -> None:
    raw = '```json\n{"action": "idle", "next_wake_in": 30}\n```'
    decision = parser.parse(raw)
    assert decision.next_wake_in == 30


def test_parse_chatty_output(parser: DecisionParser) -> None:
    raw = '我想想，就这样吧：{"action": "idle"} 好，决定了。'
    assert parser.parse(raw).action.value == "idle"


def test_parse_rejects_non_json(parser: DecisionParser) -> None:
    with pytest.raises(DecisionError):
        parser.parse("完全不是 JSON")


def test_parse_rejects_non_object(parser: DecisionParser) -> None:
    with pytest.raises(DecisionError):
        parser.parse("[1, 2, 3]")


def test_string_remember_becomes_decision_error(parser: DecisionParser) -> None:
    with pytest.raises(DecisionError):
        parser.parse('{"action": "idle", "remember": "随便一句话"}')


def test_string_call_item_becomes_decision_error(parser: DecisionParser) -> None:
    with pytest.raises(DecisionError):
        parser.parse('{"action": "tool.call", "calls": ["fs.read"]}')


def test_string_target_becomes_decision_error(parser: DecisionParser) -> None:
    with pytest.raises(DecisionError):
        parser.parse('{"action": "idle", "target": "someone"}')


# ---------------------------------------------------------------- 校验


def test_tool_call_requires_calls(parser: DecisionParser) -> None:
    with pytest.raises(DecisionError):
        parser.parse('{"action": "tool.call", "calls": []}')


def test_tool_call_limits_call_count(parser: DecisionParser) -> None:
    calls = ",".join(
        '{"capability": "fs.read", "args": {}}' for _ in range(6)
    )
    with pytest.raises(DecisionError):
        parser.parse('{"action": "tool.call", "calls": [%s]}' % calls)


def test_tool_call_rejects_blank_capability(parser: DecisionParser) -> None:
    with pytest.raises(DecisionError):
        parser.parse('{"action": "tool.call", "calls": [{"capability": "  "}]}')


def test_tool_call_accepts_five(parser: DecisionParser) -> None:
    calls = ",".join(
        '{"capability": "fs.read", "args": {}}' for _ in range(5)
    )
    decision = parser.parse('{"action": "tool.call", "calls": [%s]}' % calls)
    assert len(decision.calls) == 5


def test_chat_reply_requires_target(parser: DecisionParser) -> None:
    with pytest.raises(DecisionError):
        parser.parse('{"action": "chat.reply", "args": {"text": "嗨"}}')


def test_chat_reply_requires_text(parser: DecisionParser) -> None:
    raw = (
        '{"action": "chat.reply",'
        ' "target": {"friend_id": "u_1", "conversation_id": "c_1"},'
        ' "args": {"text": "   "}}'
    )
    with pytest.raises(DecisionError):
        parser.parse(raw)


def test_chat_reply_ok(parser: DecisionParser) -> None:
    raw = (
        '{"action": "chat.reply",'
        ' "target": {"friend_id": "u_1", "conversation_id": "c_1"},'
        ' "args": {"text": "在呢"}}'
    )
    decision = parser.parse(raw)
    assert decision.target is not None
    assert decision.target.friend_id == "u_1"
    assert decision.args["text"] == "在呢"


def test_activity_start_requires_kind_and_goal(parser: DecisionParser) -> None:
    with pytest.raises(DecisionError):
        parser.parse('{"action": "activity.start", "args": {"goal": "补测试"}}')
    with pytest.raises(DecisionError):
        parser.parse('{"action": "activity.start", "args": {"kind": "coding"}}')


def test_activity_start_ok(parser: DecisionParser) -> None:
    raw = '{"action": "activity.start", "args": {"kind": "coding", "goal": "补测试"}}'
    decision = parser.parse(raw)
    assert decision.args["kind"] == "coding"


def test_activity_abandon_rejects_bad_outcome(parser: DecisionParser) -> None:
    with pytest.raises(DecisionError):
        parser.parse('{"action": "activity.abandon", "args": {"outcome": "maybe"}}')


def test_activity_abandon_accepts_completed(parser: DecisionParser) -> None:
    decision = parser.parse(
        '{"action": "activity.abandon", "args": {"outcome": "completed"}}'
    )
    assert decision.action.value == "activity.abandon"


# ---------------------------------------------------------------- 钳制


def test_next_wake_in_is_clamped(parser: DecisionParser) -> None:
    decision = parser.parse('{"action": "idle", "next_wake_in": 999999}')
    assert decision.next_wake_in == 3600


def test_next_wake_in_negative_is_zero(parser: DecisionParser) -> None:
    decision = parser.parse('{"action": "idle", "next_wake_in": -5}')
    assert decision.next_wake_in == 0


def test_handled_drops_unknown_ids(parser: DecisionParser) -> None:
    raw = '{"action": "idle", "handled": ["ev_1", "ev_ghost"]}'
    decision = parser.parse(raw, inbox_ids=["ev_1"])
    assert decision.handled == ["ev_1"]


def test_handled_kept_without_inbox(parser: DecisionParser) -> None:
    raw = '{"action": "idle", "handled": ["ev_1", "ev_2"]}'
    decision = parser.parse(raw)
    assert decision.handled == ["ev_1", "ev_2"]


def test_progress_is_clamped(parser: DecisionParser) -> None:
    raw = '{"action": "idle", "activity_update": {"progress": 1.7}}'
    assert parser.parse(raw).activity_update.progress == 1.0
    raw = '{"action": "idle", "activity_update": {"progress": -0.3}}'
    assert parser.parse(raw).activity_update.progress == 0.0


def test_tool_call_resets_wake_schedule(parser: DecisionParser) -> None:
    raw = (
        '{"action": "tool.call", "next_wake_in": 600,'
        ' "wake_when": [{"type": "presence_changed", "args": {}}],'
        ' "calls": [{"capability": "fs.read", "args": {}}]}'
    )
    decision = parser.parse(raw)
    assert decision.next_wake_in == 0
    assert decision.wake_when == []


# ---------------------------------------------------------------- repair


def test_repair_message_mentions_error_and_raw() -> None:
    message = repair_message('{"action": "oops"}', ValueError("坏掉了"))
    assert "坏掉了" in message
    assert '{"action": "oops"}' in message
    assert "tool.call" in message
