"""cognition/decision.py 的解析、校验与钳制。"""

from __future__ import annotations

import json

import pytest

from manager.cognition.decision import DecisionParser, repair_message
from manager.shared.types import DecisionError


class _Cfg:
    next_wake_max_seconds = 3600


@pytest.fixture
def parser():
    return DecisionParser(_Cfg())


def _d(**kw):
    return json.dumps(kw)


# --- 解析 ---

def test_rejects_non_json(parser):
    with pytest.raises(DecisionError):
        parser.parse("这不是 JSON")


def test_rejects_non_object(parser):
    with pytest.raises(DecisionError):
        parser.parse("[1, 2, 3]")


def test_accepts_json_in_code_fence(parser):
    raw = "```json\n" + _d(action="idle") + "\n```"
    decision = parser.parse(raw)
    assert decision.action.value == "idle"


# --- 校验 ---

def test_tool_call_requires_calls(parser):
    with pytest.raises(DecisionError):
        parser.parse(_d(action="tool.call", calls=[]))


def test_tool_call_limits_calls(parser):
    calls = [{"capability": "fs.list", "args": {}} for _ in range(6)]
    with pytest.raises(DecisionError):
        parser.parse(_d(action="tool.call", calls=calls))


def test_tool_call_rejects_blank_capability(parser):
    with pytest.raises(DecisionError):
        parser.parse(_d(action="tool.call", calls=[{"capability": "  ", "args": {}}]))


def test_chat_reply_requires_target(parser):
    with pytest.raises(DecisionError):
        parser.parse(_d(action="chat.reply", args={"text": "hi"}))


def test_chat_reply_requires_text(parser):
    raw = _d(action="chat.reply", target={"friend_id": "u1"}, args={"text": "   "})
    with pytest.raises(DecisionError):
        parser.parse(raw)


def test_chat_reply_ok(parser):
    raw = _d(action="chat.reply", target={"friend_id": "u1"}, args={"text": "hi"})
    decision = parser.parse(raw)
    assert decision.target.friend_id == "u1"
    assert decision.args["text"] == "hi"


def test_activity_start_requires_kind_and_goal(parser):
    with pytest.raises(DecisionError):
        parser.parse(_d(action="activity.start", args={"goal": "x"}))
    with pytest.raises(DecisionError):
        parser.parse(_d(action="activity.start", args={"kind": "read"}))


def test_activity_abandon_outcome_whitelist(parser):
    with pytest.raises(DecisionError):
        parser.parse(_d(action="activity.abandon", args={"outcome": "whatever"}))
    decision = parser.parse(_d(action="activity.abandon", args={"outcome": "completed"}))
    assert decision.args["outcome"] == "completed"


# --- 钳制 ---

def test_next_wake_in_clamped_to_max(parser):
    decision = parser.parse(_d(action="idle", next_wake_in=999999))
    assert decision.next_wake_in == 3600


def test_next_wake_in_negative_becomes_zero(parser):
    decision = parser.parse(_d(action="idle", next_wake_in=-5))
    assert decision.next_wake_in == 0


def test_handled_keeps_only_known_ids(parser):
    raw = _d(action="idle", handled=["ev_1", "ev_2"])
    decision = parser.parse(raw, inbox_ids=["ev_1"])
    assert decision.handled == ["ev_1"]


def test_activity_progress_clamped(parser):
    raw = _d(
        action="activity.complete",
        activity_update={"progress": 1.7},
    )
    decision = parser.parse(raw)
    assert decision.activity_update.progress == 1.0


def test_tool_call_forces_immediate_next_frame(parser):
    raw = _d(
        action="tool.call",
        calls=[{"capability": "fs.list", "args": {"path": "."}}],
        next_wake_in=600,
    )
    decision = parser.parse(raw)
    assert decision.next_wake_in == 0
    assert decision.wake_when == []


# --- repair ---

def test_repair_message_contains_error_and_raw():
    msg = repair_message("oops", ValueError("bad json"))
    assert "bad json" in msg
    assert "oops" in msg
    assert "tool.call" in msg
