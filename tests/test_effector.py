"""Effector 单元测试：只验证 Decision 如何落地，不重新判断。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from manager.effector.effector import Effector
from manager.shared.types import (
    Action,
    ActivityUpdate,
    CapabilityCall,
    Decision,
    Remember,
    Target,
)

TZ = timezone(timedelta(hours=8))
NOW = datetime(2026, 10, 21, 11, 0, tzinfo=TZ)


class MemoryStub:
    def __init__(self, fail: bool = False) -> None:
        self.calls = []
        self.fail = fail

    def remember(self, remember, *, speaker=None, conversation=None, at=None):
        if self.fail:
            raise RuntimeError("memory boom")
        self.calls.append(
            {"text": remember.text, "speaker": speaker, "conversation": conversation, "at": at}
        )


class ChatSenderStub:
    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self.sent = []
        self.reached = []

    def _delivery(self):
        return SimpleNamespace(ok=self.ok, detail=None if self.ok else "失败")

    def send(self, target, text):
        self.sent.append((target, text))
        return self._delivery()

    def reach_out(self, target, text):
        self.reached.append((target, text))
        return self._delivery()


class BodyStub:
    def __init__(self, satiety: float = 50.0) -> None:
        self.satiety = satiety
        self.modes = []

    def set_mode(self, mode):
        self.modes.append(mode)


class ActivitiesStub:
    def __init__(self, fail_update: bool = False) -> None:
        self.fail_update = fail_update
        self.updated = []
        self.started = []
        self.paused = []
        self.resumed = []
        self.completed = []
        self.abandoned = []

    def update(self, update, activity_id=None):
        if self.fail_update:
            from manager.shared.types import ActivityError
            raise ActivityError("update boom")
        self.updated.append({"update": update, "activity_id": activity_id})
        return SimpleNamespace(id=activity_id, progress=update.progress)

    def start(self, kind, goal, steps_limit=8, note=None, state=None):
        self.started.append({"kind": kind, "goal": goal, "steps_limit": steps_limit, "note": note, "state": state})
        return SimpleNamespace(id="act1", kind=kind, goal=goal)

    def pause(self, activity_id, note=None, resume_when=None):
        self.paused.append({"activity_id": activity_id, "note": note, "resume_when": resume_when})
        return SimpleNamespace(id=activity_id)

    def resume(self, activity_id):
        self.resumed.append(activity_id)
        return SimpleNamespace(id=activity_id)

    def complete(self, activity_id, outcome="completed", summary=None):
        self.completed.append({"activity_id": activity_id, "outcome": outcome, "summary": summary})
        return SimpleNamespace(id=activity_id)

    def abandon(self, activity_id, outcome="abandoned", summary=None):
        self.abandoned.append({"activity_id": activity_id, "outcome": outcome, "summary": summary})
        return SimpleNamespace(id=activity_id)


class CapsStub:
    def __init__(self, value=None, fail: bool = False) -> None:
        self.value = value
        self.fail = fail
        self.calls = []

    def submit(self, call, caller=""):
        if self.fail:
            raise RuntimeError("submit boom")
        self.calls.append((call, caller))
        return self.value


def make_effector(
    *,
    memory=None,
    chat_sender=None,
    body=None,
    activities=None,
    capabilities=None,
    eat_handler=None,
):
    return Effector(
        SimpleNamespace(),
        memory=memory or MemoryStub(),
        activities=activities or ActivitiesStub(),
        body=body or BodyStub(),
        chat_sender=chat_sender or ChatSenderStub(),
        capabilities=capabilities,
        eat_handler=eat_handler,
        now_fn=lambda: NOW,
    )


def target():
    return Target(friend_id="u_1", conversation_id="c_1")


# ---------------------------------------------------------------------
# 记忆
# ---------------------------------------------------------------------


def test_remember_is_written_with_target_context():
    memory = MemoryStub()
    effector = make_effector(memory=memory)
    decision = Decision(
        action=Action.CHAT_REPLY,
        target=target(),
        args={"text": "嗨"},
        remember=Remember(text="聊了一句"),
    )
    result = effector.apply(decision, caller="tester")
    assert result.ok
    assert memory.calls == [
        {"text": "聊了一句", "speaker": "u_1", "conversation": "c_1", "at": NOW}
    ]


def test_remember_failure_does_not_block_chat():
    memory = MemoryStub(fail=True)
    chat_sender = ChatSenderStub()
    effector = make_effector(memory=memory, chat_sender=chat_sender)
    decision = Decision(
        action=Action.CHAT_REPLY,
        target=target(),
        args={"text": "嗨"},
        remember=Remember(text="会炸的", ),
    )
    result = effector.apply(decision)
    assert not result.ok
    assert any("记忆写入失败" in e for e in result.errors)
    assert chat_sender.sent == [(target(), "嗨")]


# ---------------------------------------------------------------------
# 聊天
# ---------------------------------------------------------------------


def test_chat_reply_uses_send():
    chat_sender = ChatSenderStub()
    effector = make_effector(chat_sender=chat_sender)
    decision = Decision(action=Action.CHAT_REPLY, target=target(), args={"text": "好呀"})
    result = effector.apply(decision)
    assert result.ok
    assert chat_sender.sent == [(target(), "好呀")]
    assert chat_sender.reached == []
    assert result.delivery.ok


def test_chat_reach_out_uses_reach_out():
    chat_sender = ChatSenderStub()
    effector = make_effector(chat_sender=chat_sender)
    decision = Decision(action=Action.CHAT_REACH_OUT, target=target(), args={"text": "在吗"})
    effector.apply(decision)
    assert chat_sender.reached == [(target(), "在吗")]
    assert chat_sender.sent == []


def test_chat_without_target_records_error():
    chat_sender = ChatSenderStub()
    effector = make_effector(chat_sender=chat_sender)
    decision = Decision(action=Action.CHAT_REPLY, args={"text": "没人可发"})
    result = effector.apply(decision)
    assert not result.ok
    assert any("缺少 target" in e for e in result.errors)
    assert chat_sender.sent == []


def test_speaker_falls_back_to_target():
    memory = MemoryStub()
    effector = make_effector(memory=memory)
    decision = Decision(
        action=Action.IDLE,
        target=target(),
        remember=Remember(text="安静待着"),
    )
    effector.apply(decision)
    assert memory.calls[0]["speaker"] == "u_1"
    assert memory.calls[0]["conversation"] == "c_1"


# ---------------------------------------------------------------------
# 能力调用
# ---------------------------------------------------------------------


def test_tool_call_counts_pending():
    caps = CapsStub(value=None)
    effector = make_effector(capabilities=caps)
    call = CapabilityCall(capability="fs.read", args={"path": "a.py"})
    decision = Decision(action=Action.TOOL_CALL, calls=[call])
    result = effector.apply(decision, caller="brain")
    assert result.ok
    assert result.pending_capabilities == 1
    assert result.capability_results == []
    assert caps.calls[0][1] == "brain"


def test_tool_call_collects_sync_result():
    value = SimpleNamespace(ok=True, summary="done")
    caps = CapsStub(value=value)
    effector = make_effector(capabilities=caps)
    call = CapabilityCall(capability="time.now", args={})
    decision = Decision(action=Action.TOOL_CALL, calls=[call])
    result = effector.apply(decision)
    assert result.capability_results == [value]
    assert result.capability_calls == [call]
    assert result.pending_capabilities == 0


def test_tool_call_submit_error_and_missing_executor():
    decision = Decision(action=Action.TOOL_CALL, calls=[CapabilityCall(capability="x")])

    missing = make_effector(capabilities=None).apply(decision)
    assert any("能力执行器未配置" in e for e in missing.errors)

    failing = make_effector(capabilities=CapsStub(fail=True)).apply(decision)
    assert any("x: submit boom" in e for e in failing.errors)


# ---------------------------------------------------------------------
# 进食
# ---------------------------------------------------------------------


def test_eat_ok():
    foods = []
    effector = make_effector(eat_handler=lambda food: foods.append(food) or True)
    result = effector.apply(Decision(action=Action.EAT, args={"food": "苹果"}))
    assert result.ok
    assert result.ate == "苹果"
    assert foods == ["苹果"]


def test_eat_default_food_and_rejection():
    effector = make_effector(eat_handler=lambda food: True)
    assert effector.apply(Decision(action=Action.EAT)).ate == "胡萝卜"

    rejected = make_effector(eat_handler=lambda food: False)
    result = rejected.apply(Decision(action=Action.EAT))
    assert any("没有接受进食请求" in e for e in result.errors)


def test_eat_without_handler():
    effector = make_effector(eat_handler=None)
    result = effector.apply(Decision(action=Action.EAT))
    assert any("未连接桌宠身体" in e for e in result.errors)


# ---------------------------------------------------------------------
# 活动
# ---------------------------------------------------------------------


def test_activity_start_delegates():
    activities = ActivitiesStub()
    effector = make_effector(activities=activities)
    decision = Decision(
        action=Action.ACTIVITY_START,
        args={"kind": "coding", "goal": "写测试", "steps_limit": 5, "note": "effector"},
    )
    result = effector.apply(decision)
    assert result.ok
    assert activities.started[0]["kind"] == "coding"
    assert activities.started[0]["goal"] == "写测试"
    assert activities.started[0]["steps_limit"] == 5


def test_activity_update_and_error():
    activities = ActivitiesStub()
    effector = make_effector(activities=activities)
    decision = Decision(
        action=Action.IDLE,
        activity_update=ActivityUpdate(progress=0.5, note="半程"),
        args={"activity_id": "act1"},
    )
    result = effector.apply(decision)
    assert result.ok
    assert activities.updated[0]["activity_id"] == "act1"
    assert activities.updated[0]["update"].progress == 0.5

    broken = make_effector(activities=ActivitiesStub(fail_update=True))
    failure = broken.apply(decision)
    assert any("update boom" in e for e in failure.errors)


# ---------------------------------------------------------------------
# 模式切换
# ---------------------------------------------------------------------


def test_rest_sets_rest_mode():
    body = BodyStub()
    effector = make_effector(body=body)
    effector.apply(Decision(action=Action.REST))
    assert body.modes == ["rest"]


def test_active_action_sets_awake_and_idle_keeps_mode():
    body = BodyStub()
    effector = make_effector(body=body, chat_sender=ChatSenderStub())
    effector.apply(Decision(action=Action.CHAT_REPLY, target=target(), args={"text": "嗯"}))
    assert body.modes == ["awake"]

    idle_body = BodyStub()
    make_effector(body=idle_body).apply(Decision(action=Action.IDLE))
    assert idle_body.modes == []
