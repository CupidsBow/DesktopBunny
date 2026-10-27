"""模拟 Brain 真实运行环境的行为测试。

不依赖真实 LLM / QQ 网关 / 桌宠本体：用 FakeLLM 脚本化决策、FakeTransport 记录发送、
临时目录隔离磁盘状态，驱动 Brain 走完 new_brain.md 的核心闭环，验证重构后的行为
逻辑没有重大偏差。

覆盖：
  - 启动 + 被动回复（chat.reply）
  - tool.call 多帧（同步能力 → CAPABILITY_DONE → 终态回复）
  - eat 能力（桌宠主线程进食请求）
  - chat.reach_out 主动搭话
  - activity 生命周期（start / complete）
  - 闹钟唤醒（next_wake_in → 到期 → 二次帧）
  - 待机 idle 终态（静默 + next_wake_in 再唤醒）
  - 事件账本与活动的持久化 / 重载

运行：
  python -m test.test_brain_runtime            # 简洁输出
  python -m test.test_brain_runtime -v         # 附带 brain 的帧日志（验证日志追踪）
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.qq_adapter import QQAdapter
from manager.brain import Brain
from manager.mesh.mesh import MeshService
from manager.shared.config import BrainConfig, BrainPaths
from manager.shared.timeutil import to_iso
from manager.shared.types import (
    ActivityStatus,
    Event,
    EventKind,
    EventSource,
)

_FENCE = re.compile(r"```json\n(.*?)\n```", re.DOTALL)


class Clock:
    """可控时钟：默认固定，避免定期唤醒/时间流逝干扰测试。"""

    def __init__(self, start: datetime | None = None):
        self._now = start or datetime(2026, 10, 24, 12, 0, 0).astimezone()

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)


def _situation(messages) -> dict:
    user = messages[-1]["content"] if messages else ""
    match = _FENCE.search(user)
    if not match:
        return {}
    try:
        return json.loads(match.group(1))
    except ValueError:
        return {}


def _message_event(situation: dict) -> dict:
    for event in situation.get("inbox", []):
        if event.get("kind") == "message":
            return event
    raise AssertionError("inbox 里没有 message 事件")


def _target_from(event: dict) -> dict:
    return {
        "friend_id": event["friend_id"],
    }


class FakeLLM:
    """脚本化 LLM：认知帧按脚本顺序出决策；记忆整理类 prompt 返回空操作。"""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def chat(self, messages, timeout=None, temperature=None):
        self.calls.append(list(messages))
        system = messages[0]["content"] if messages else ""
        if "记忆整理程序" in system:
            return '{"operations": []}'
        if not self.script:
            raise AssertionError("FakeLLM 脚本耗尽（多出一次认知帧）")
        item = self.script.pop(0)
        return (
            item(messages)
            if callable(item)
            else json.dumps(item, ensure_ascii=False)
        )


class FakeTransport:
    """记录 Alice 发出的消息，替代 QQ REST 发送。"""

    def __init__(self):
        self.sent = []

    def send_message(self, friend_id, msg_id, content, msg_type=0):
        self.sent.append(
            {
                "to": friend_id,
                "msg_id": msg_id,
                "text": content,
                "msg_type": msg_type,
            }
        )
        return True


def make_brain(tmpdir, llm, transport, *, eat_handler=None, clock=None):
    clock = clock or Clock()
    return Brain(
        paths=BrainPaths(root=tmpdir),
        llm=llm,
        transport=transport,
        snapshot_getter=lambda: {"satiety": 80.0},
        eat_handler=eat_handler,
        now_fn=clock.now,
        enable_logging=False,
    )


def wait_until(predicate, timeout=8.0, interval=0.02):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def wait_startup(brain, llm, transport):
    """等启动帧落地（首个 reach_out 发出），随后清空发送记录，隔离启动噪声。"""
    assert wait_until(lambda: transport.sent), "启动帧未在时限内完成"
    transport.sent.clear()


# ---------------------------------------------------------------------------
# 脚本化决策工厂（每个返回 (messages) -> decision_json）
# ---------------------------------------------------------------------------
def _startup_decision(messages):
    """启动帧：向占位好友主动搭话并处理掉 startup 系统事件，结束该帧。"""
    sit = _situation(messages)
    return json.dumps(
        {
            "action": "chat.reach_out",
            "target": {
                "friend_id": "u_startup",
            },
            "args": {"text": "（启动）"},
            "handled": [e["id"] for e in sit.get("inbox", [])],
        },
        ensure_ascii=False,
    )


def _reply(text):
    def _fn(messages):
        sit = _situation(messages)
        event = _message_event(sit)
        return json.dumps(
            {
                "action": "chat.reply",
                "target": _target_from(event),
                "args": {"text": text},
                "handled": [e["id"] for e in sit.get("inbox", [])],
            },
            ensure_ascii=False,
        )

    return _fn


def _tool_call(capability, args=None):
    def _fn(messages):
        return json.dumps(
            {
                "action": "tool.call",
                "calls": [{"capability": capability, "args": args or {}}],
                "handled": [],
            },
            ensure_ascii=False,
        )

    return _fn


def _reach_out(text, friend_id="u_123"):
    def _fn(messages):
        sit = _situation(messages)
        event = next(
            (e for e in sit.get("inbox", []) if e.get("kind") == "message"), None
        )
        target = (
            _target_from(event)
            if event
            else {
                "friend_id": friend_id,
            }
        )
        return json.dumps(
            {
                "action": "chat.reach_out",
                "target": target,
                "args": {"text": text},
                "handled": [e["id"] for e in sit.get("inbox", [])],
            },
            ensure_ascii=False,
        )

    return _fn


def _activity_start(kind="learn", goal="了解 Python 装饰器"):
    def _fn(messages):
        sit = _situation(messages)
        return json.dumps(
            {
                "action": "activity.start",
                "args": {"kind": kind, "goal": goal},
                "handled": [e["id"] for e in sit.get("inbox", [])],
            },
            ensure_ascii=False,
        )

    return _fn


def _activity_complete(summary="学会了"):
    def _fn(messages):
        sit = _situation(messages)
        return json.dumps(
            {
                "action": "activity.complete",
                "args": {"summary": summary},
                "handled": [e["id"] for e in sit.get("inbox", [])],
            },
            ensure_ascii=False,
        )

    return _fn


def _reply_with_alarm(text, next_wake_in):
    def _fn(messages):
        sit = _situation(messages)
        event = _message_event(sit)
        return json.dumps(
            {
                "action": "chat.reply",
                "target": _target_from(event),
                "args": {"text": text},
                "handled": [e["id"] for e in sit.get("inbox", [])],
                "next_wake_in": next_wake_in,
            },
            ensure_ascii=False,
        )

    return _fn


def _idle(next_wake_in=None):
    def _fn(messages):
        sit = _situation(messages)
        decision = {
            "action": "idle",
            "handled": [e["id"] for e in sit.get("inbox", [])],
        }
        if next_wake_in is not None:
            decision["next_wake_in"] = next_wake_in
        return json.dumps(decision, ensure_ascii=False)

    return _fn


# ---------------------------------------------------------------------------
# 测试用例
# ---------------------------------------------------------------------------
def test_passive_reply(tmpdir):
    llm = FakeLLM([_startup_decision, _reply("你好呀，我在的。")])
    transport = FakeTransport()
    brain = make_brain(tmpdir, llm, transport)
    brain.start()
    try:
        wait_startup(brain, llm, transport)
        QQAdapter(brain).submit_message("u_123", "m_1", "你好")
        assert wait_until(
            lambda: any(m["text"] == "你好呀，我在的。" for m in transport.sent)
        ), "被动回复未发出"
        sent = next(m for m in transport.sent if m["text"] == "你好呀，我在的。")
        assert sent["msg_id"] == "m_1", "被动回复应带来信 msg_id"
        assert sent["to"] == "u_123", "回复应发到来信好友"
        assert "你好呀，我在的。" in brain.memory.recent_context(), "回复未写入 L1"
        assert not brain.events.unhandled(), "消息事件应已标记处理"
    finally:
        brain.stop("测试结束")


def test_tool_call_then_reply(tmpdir):
    llm = FakeLLM(
        [_startup_decision, _tool_call("time.now"), _reply("查好了。")]
    )
    transport = FakeTransport()
    brain = make_brain(tmpdir, llm, transport)
    brain.start()
    try:
        wait_startup(brain, llm, transport)
        QQAdapter(brain).submit_message("u_123", "m_1", "现在几点")
        assert wait_until(
            lambda: any(m["text"] == "查好了。" for m in transport.sent)
        ), "tool.call 后未回复"
        assert len(llm.calls) >= 3, "应有 启动 + tool.call + 终态回复 三次认知帧"
        records = brain.audit.tail(20)
        assert any(r["capability"] == "time.now" for r in records), "time.now 未审计"
    finally:
        brain.stop("测试结束")


def test_eat_capability(tmpdir):
    eaten = []
    llm = FakeLLM(
        [
            _startup_decision,
            _tool_call("eat", {"food": "胡萝卜"}),
            _reply("吃饱了。"),
        ]
    )
    transport = FakeTransport()
    brain = make_brain(
        tmpdir,
        llm,
        transport,
        eat_handler=lambda food: eaten.append(food) or True,
    )
    brain.start()
    try:
        wait_startup(brain, llm, transport)
        QQAdapter(brain).submit_message("u_123", "m_1", "饿了")
        assert wait_until(lambda: eaten == ["胡萝卜"]), "eat 能力未触发进食"
        assert wait_until(
            lambda: any(m["text"] == "吃饱了。" for m in transport.sent)
        ), "eat 之后的终态回复未发出"
    finally:
        brain.stop("测试结束")


def test_reach_out(tmpdir):
    llm = FakeLLM([_startup_decision, _reach_out("突然想起你了。")])
    transport = FakeTransport()
    brain = make_brain(tmpdir, llm, transport)
    brain.start()
    try:
        wait_startup(brain, llm, transport)
        QQAdapter(brain).submit_message("u_123", "m_1", "在吗")
        assert wait_until(
            lambda: any(m["text"] == "突然想起你了。" for m in transport.sent)
        ), "reach_out 未发出"
        sent = next(m for m in transport.sent if m["text"] == "突然想起你了。")
        assert sent["msg_id"] is None, "reach_out 应不带 msg_id（主动消息）"
    finally:
        brain.stop("测试结束")


def test_activity_lifecycle(tmpdir):
    llm = FakeLLM(
        [
            _startup_decision,
            _activity_start("learn", "了解 Python 装饰器"),
            _activity_complete("学会了"),
        ]
    )
    transport = FakeTransport()
    brain = make_brain(tmpdir, llm, transport)
    brain.start()
    try:
        wait_startup(brain, llm, transport)
        QQAdapter(brain).submit_message("u_123", "m_1", "开始学习吧")
        assert wait_until(lambda: brain.activities.active() is not None), "活动未开始"
        act = brain.activities.active()
        assert act.kind == "learn" and act.goal == "了解 Python 装饰器"

        QQAdapter(brain).submit_message("u_123", "m_2", "学完了")
        assert wait_until(lambda: brain.activities.active() is None), "活动未结束"
        assert (
            brain.activities.get(act.id).status is ActivityStatus.COMPLETED
        ), "活动状态应为 completed"
    finally:
        brain.stop("测试结束")


def test_alarm_wake(tmpdir):
    clock = Clock()
    llm = FakeLLM(
        [
            _startup_decision,
            _reply_with_alarm("待会儿再聊", next_wake_in=1),
            _reach_out("时间到了。"),
        ]
    )
    transport = FakeTransport()
    brain = make_brain(tmpdir, llm, transport, clock=clock)
    brain.start()
    try:
        wait_startup(brain, llm, transport)
        QQAdapter(brain).submit_message("u_123", "m_1", "先忙")
        assert wait_until(
            lambda: any(m["text"] == "待会儿再聊" for m in transport.sent)
        ), "首次回复未发出"
        clock.advance(2.0)
        assert wait_until(
            lambda: any(m["text"] == "时间到了。" for m in transport.sent)
        ), "闹钟唤醒未触发二次帧"
    finally:
        brain.stop("测试结束")


def test_idle(tmpdir):
    clock = Clock()
    llm = FakeLLM(
        [_startup_decision, _idle(next_wake_in=1), _reach_out("刚才没想说啥。")]
    )
    transport = FakeTransport()
    brain = make_brain(tmpdir, llm, transport, clock=clock)
    brain.start()
    try:
        wait_startup(brain, llm, transport)
        QQAdapter(brain).submit_message("u_123", "m_1", "在吗")
        assert wait_until(lambda: not brain.events.unhandled()), "idle 帧未处理事件"
        assert transport.sent == [], "idle 不应发出任何消息"
        clock.advance(2.0)
        assert wait_until(
            lambda: any(m["text"] == "刚才没想说啥。" for m in transport.sent)
        ), "idle 的 next_wake_in 未触发后续唤醒"
    finally:
        brain.stop("测试结束")


def test_mesh_roundtrip(tmpdir):
    peers_root = os.path.join(tmpdir, "peers")
    clock = Clock()
    llm = FakeLLM([_startup_decision, _reply("收到啦！")])
    transport = FakeTransport()
    bob = MeshService(peers_root, slug="Bob", name="Bob", now_fn=clock.now)
    alice = MeshService(peers_root, slug="Alice", name="Alice", now_fn=clock.now)
    brain = Brain(
        paths=BrainPaths(root=os.path.join(tmpdir, "alice")),
        llm=llm,
        transport=transport,
        mesh=alice,
        snapshot_getter=lambda: {"satiety": 80.0},
        now_fn=clock.now,
        enable_logging=False,
    )
    brain.start()
    try:
        wait_startup(brain, llm, transport)
        bob.heartbeat()
        alice.heartbeat()
        assert any(p.slug == "Alice" for p in bob.discover()), "Bob 未发现 Alice"
        assert any(p.slug == "Bob" for p in alice.discover()), "Alice 未发现 Bob"

        bob.send_message("peer:Alice", "m_bob_1", "在吗")
        clock.advance(2.0)
        brain.tick(2.0)

        def alice_replied():
            return any("收到啦！" in m.text for m in bob.poll_inbox())

        assert wait_until(alice_replied), "Alice 未通过本地通道回复 Bob"
        assert brain.memory.friend("peer:Bob").first_seen, "Bob 未进入 Alice 的朋友记忆"
    finally:
        brain.stop("测试结束")


def test_peer_online_wake(tmpdir):
    peers_root = os.path.join(tmpdir, "peers")
    clock = Clock()
    llm = FakeLLM([_startup_decision, _idle()])
    transport = FakeTransport()
    bob = MeshService(peers_root, slug="Bob", name="Bob", now_fn=clock.now)
    alice = MeshService(peers_root, slug="Alice", name="Alice", now_fn=clock.now)
    brain = Brain(
        paths=BrainPaths(root=os.path.join(tmpdir, "alice")),
        llm=llm,
        transport=transport,
        mesh=alice,
        snapshot_getter=lambda: {"satiety": 80.0},
        now_fn=clock.now,
        enable_logging=False,
    )
    brain.start()
    try:
        wait_startup(brain, llm, transport)
        bob.heartbeat()
        alice.heartbeat()
        clock.advance(2.0)
        brain.tick(2.0)

        def peer_online_signal():
            return any(
                e.kind == EventKind.SIGNAL
                and e.payload.get("signal") == "peer_online"
                and e.payload.get("slug") == "Bob"
                for e in brain.events.all()
            )

        assert peer_online_signal(), "peer 上线未触发 peer_online 信号"
        assert wait_until(lambda: len(llm.calls) >= 2), "peer 上线未唤醒认知帧"
    finally:
        brain.stop("测试结束")


def test_inbox_cap_drains(tmpdir):
    clock = Clock()
    llm = FakeLLM([_startup_decision] + [_reply("收到") for _ in range(4)])
    transport = FakeTransport()
    config = BrainConfig(
        paths=BrainPaths(root=os.path.join(tmpdir, "alice")),
        inbox_max_events=2,
    )
    brain = Brain(
        config=config,
        llm=llm,
        transport=transport,
        snapshot_getter=lambda: {"satiety": 80.0},
        now_fn=clock.now,
        enable_logging=False,
    )
    brain.start()
    try:
        wait_startup(brain, llm, transport)
        # 3 条静默入队，最后 1 条走 submit_event 触发一次唤醒，确保 4 条同时待处理。
        for i in range(3):
            brain.events.add(
                Event(
                    id="",
                    kind=EventKind.MESSAGE,
                    at=to_iso(clock.now()),
                    source=EventSource(friend_id="u_123"),
                    payload={"msg_id": f"m_{i}", "text": f"消息{i}"},
                )
            )
        brain.submit_event(
            Event(
                id="",
                kind=EventKind.MESSAGE,
                at=to_iso(clock.now()),
                source=EventSource(friend_id="u_123"),
                payload={"msg_id": "m_3", "text": "消息3"},
            )
        )

        assert wait_until(lambda: not brain.events.unhandled()), "积压未排空"

        for messages in llm.calls:
            inbox = _situation(messages).get("inbox", [])
            assert len(inbox) <= 2, f"inbox 超过上限：{len(inbox)}"
    finally:
        brain.stop("测试结束")


def test_persistence_reload(tmpdir):
    # 第一次会话：处理一条消息并开始活动，随后模拟进程退出
    llm = FakeLLM([_startup_decision, _activity_start("learn", "没学完的任务")])
    transport = FakeTransport()
    brain = make_brain(tmpdir, llm, transport)
    brain.start()
    wait_startup(brain, llm, transport)
    QQAdapter(brain).submit_message("u_123", "m_1", "开始学习")
    assert wait_until(lambda: brain.activities.active() is not None)
    brain.stop("崩溃模拟")

    # 第二次会话：同一目录重载，活动应恢复为 paused，事件账本应保留
    llm2 = FakeLLM([_startup_decision])
    transport2 = FakeTransport()
    brain2 = make_brain(tmpdir, llm2, transport2)
    brain2.start()
    try:
        paused = brain2.activities.paused()
        assert paused is not None, "重启后未恢复未完成的活动"
        assert paused.status is ActivityStatus.PAUSED, "恢复的活动应为 paused"
        assert brain2.events.all(), "事件账本未持久化"
    finally:
        brain2.stop("测试结束")


def test_auto_episode(tmpdir):
    """终态帧自动落一条 L2 情景；决策里不再有 remember 字段。"""
    clock = Clock()
    llm = FakeLLM([_startup_decision, _reply("记得看那篇文章。")])
    transport = FakeTransport()
    brain = make_brain(tmpdir, llm, transport, clock=clock)
    brain.start()
    try:
        wait_startup(brain, llm, transport)
        QQAdapter(brain).submit_message("u_123", "m_1", "那篇文章叫啥")
        assert wait_until(
            lambda: any(m["text"] == "记得看那篇文章。" for m in transport.sent)
        ), "回复未发出"

        def replied_episode():
            return any(
                "记得看那篇文章" in episode.body
                for episode in brain.memory.store.list_episodes()
            )

        assert wait_until(replied_episode), "终态帧没有自动写入情景"

        # 情绪取自当帧派生状态，不由模型自报
        episode = next(
            episode
            for episode in brain.memory.store.list_episodes()
            if "记得看那篇文章" in episode.body
        )
        assert episode.metadata.speaker == "u_123", "情景未记住对话对象"
        assert episode.metadata.emotion, "情景缺少情绪标签"

        # 输出协议里不该再出现 remember
        prompts = " ".join(
            message["content"] for message in llm.calls[-1]
        )
        assert "remember" not in prompts, "prompt 里仍残留 remember 字段"
    finally:
        brain.stop("测试结束")


def test_startup_writes_no_episode(tmpdir):
    """启动帧不写情景；之后的正常帧照写。"""
    clock = Clock()
    llm = FakeLLM([_startup_decision, _reply("在的。")])
    transport = FakeTransport()
    brain = make_brain(tmpdir, llm, transport, clock=clock)
    brain.start()
    try:
        wait_startup(brain, llm, transport)
        time.sleep(0.3)
        assert (
            brain.memory.store.list_episodes() == []
        ), "启动帧不应写入情景"

        QQAdapter(brain).submit_message("u_123", "m_1", "在吗")
        assert wait_until(
            lambda: any(m["text"] == "在的。" for m in transport.sent)
        ), "回复未发出"
        assert wait_until(
            lambda: brain.memory.store.list_episodes()
        ), "正常帧未写入情景"
    finally:
        brain.stop("测试结束")


def test_frame_failure_retries(tmpdir):
    """帧失败后自动重试，而不是等 1800 秒的定期心跳。"""
    clock = Clock()
    # 一帧坏输出会触发一次「修复调用」，两次都坏这一帧才算失败。
    broken = lambda messages: "这不是 JSON"  # noqa: E731
    llm = FakeLLM(
        [
            _startup_decision,
            broken,
            broken,
            _reply("重试成功了。"),
        ]
    )
    transport = FakeTransport()
    brain = make_brain(tmpdir, llm, transport, clock=clock)
    brain.start()
    try:
        wait_startup(brain, llm, transport)
        QQAdapter(brain).submit_message("u_123", "m_1", "在吗")
        assert wait_until(lambda: len(llm.calls) >= 2), "失败帧未被调用"
        time.sleep(0.3)
        assert transport.sent == [], "失败帧不应发出任何消息"
        assert brain._frame_failures == 1, "失败未计数"

        # 退避期内不会立刻重试
        assert not any(m["text"] == "重试成功了。" for m in transport.sent)

        clock.advance(6.0)
        assert wait_until(
            lambda: any(m["text"] == "重试成功了。" for m in transport.sent)
        ), "帧失败后没有自动重试"
        assert brain._frame_failures == 0, "重试成功后计数未清零"
    finally:
        brain.stop("测试结束")


def test_related_memories_in_situation(tmpdir):
    """相关召回由装配层自动完成，不必 LLM 主动调工具。"""
    clock = Clock()
    llm = FakeLLM(
        [
            _startup_decision,
            _reply("记得看那篇文章。"),
            _reply("就是上次说的那篇。"),
        ]
    )
    transport = FakeTransport()
    brain = make_brain(tmpdir, llm, transport, clock=clock)
    brain.start()
    try:
        wait_startup(brain, llm, transport)
        QQAdapter(brain).submit_message("u_123", "m_1", "那篇文章叫啥")
        assert wait_until(
            lambda: any("那篇文章" in m["text"] for m in transport.sent)
        ), "首轮回复未发出"
        assert wait_until(
            lambda: brain.memory.store.list_episodes()
        ), "情景未落盘"

        QQAdapter(brain).submit_message("u_123", "m_2", "文章名字再发我一下")
        assert wait_until(lambda: len(llm.calls) >= 3), "第二帧未触发"

        related = _situation(llm.calls[-1]).get("memories", {}).get("related", [])
        assert related, "情境里没有自动召回的相关记忆"
        assert any("文章" in str(item.get("summary")) for item in related)
    finally:
        brain.stop("测试结束")


def test_memory_capabilities(tmpdir):
    """记忆全部前置：没有 memory.* 能力，语义与召回都由装配层给。"""
    clock = Clock()
    llm = FakeLLM([_startup_decision, _reply("在的。")])
    transport = FakeTransport()
    brain = make_brain(tmpdir, llm, transport, clock=clock)
    brain.start()
    try:
        wait_startup(brain, llm, transport)
        names = brain.registry.names()
        assert not [name for name in names if name.startswith("memory.")], (
            f"记忆不该再作为能力暴露：{names}"
        )

        QQAdapter(brain).submit_message("u_123", "m_1", "在吗")
        assert wait_until(
            lambda: any(m["text"] == "在的。" for m in transport.sent)
        ), "回复未发出"
        memories = _situation(llm.calls[-1]).get("memories", {})
        assert "friends" in memories, "情境缺少好友摘要"
        assert "related" in memories, "情境缺少相关召回"
    finally:
        brain.stop("测试结束")


def test_silent_idle_episode(tmpdir):
    """idle 看到消息却没回时留痕；纯发呆不写记忆。"""
    clock = Clock()
    llm = FakeLLM([_startup_decision, _idle(), _idle()])
    transport = FakeTransport()
    brain = make_brain(tmpdir, llm, transport, clock=clock)
    brain.start()
    try:
        wait_startup(brain, llm, transport)
        QQAdapter(brain).submit_message("u_123", "m_1", "在吗")
        assert wait_until(lambda: not brain.events.unhandled()), "idle 帧未处理事件"

        def silence_episode():
            return any(
                "没回复" in episode.description
                for episode in brain.memory.store.list_episodes()
            )

        assert wait_until(silence_episode), "被忽略的消息没有留下情景"

        before = len(brain.memory.store.list_episodes())
        # 再来一帧纯发呆（无事件），不应增加情景
        brain.submit_event(
            Event(
                id="",
                kind=EventKind.SYSTEM,
                at=to_iso(clock.now()),
                payload={"event": "test_ping"},
            )
        )
        assert wait_until(lambda: len(llm.calls) >= 3), "第三帧未触发"
        time.sleep(0.2)
        assert (
            len(brain.memory.store.list_episodes()) == before
        ), "纯发呆不应写入情景"
    finally:
        brain.stop("测试结束")


_TESTS = [
    ("被动回复 chat.reply", test_passive_reply),
    ("tool.call 多帧", test_tool_call_then_reply),
    ("eat 能力", test_eat_capability),
    ("主动搭话 chat.reach_out", test_reach_out),
    ("活动生命周期", test_activity_lifecycle),
    ("闹钟唤醒", test_alarm_wake),
    ("待机 idle 终态", test_idle),
    ("本地兔子网格往返", test_mesh_roundtrip),
    ("peer 上线唤醒", test_peer_online_wake),
    ("inbox 上限并逐帧排空", test_inbox_cap_drains),
    ("持久化与重载", test_persistence_reload),
    ("终态帧自动写入情景", test_auto_episode),
    ("启动帧不写情景", test_startup_writes_no_episode),
    ("沉默的 idle 留痕但不记发呆", test_silent_idle_episode),
    ("帧失败自动重试", test_frame_failure_retries),
    ("召回自动注入情境", test_related_memories_in_situation),
    ("记忆能力只留读档案", test_memory_capabilities),
]


def _run():
    verbose = "-v" in sys.argv or "--verbose" in sys.argv
    logging.basicConfig(
        level=logging.INFO if verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    failures = []
    for name, fn in _TESTS:
        with tempfile.TemporaryDirectory() as tmpdir:
            try:
                fn(tmpdir)
                print(f"PASS  {name}")
            except Exception as exc:  # noqa: BLE001 - 测试脚本汇总失败
                failures.append((name, exc))
                print(f"FAIL  {name}: {exc!r}")
    print(f"\n{len(_TESTS) - len(failures)}/{len(_TESTS)} 通过")
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    _run()
