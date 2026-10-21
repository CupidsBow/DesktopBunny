"""无头端到端测试：FakeLLM + FakeTransport + 临时 BrainPaths。"""

from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest

from bot.qq_adapter import QQAdapter
from manager.brain import Brain
from manager.shared.config import BrainConfig, BrainPaths
from manager.shared.types import Capability


class FakeLLM:
    def __init__(self, script: list[dict]) -> None:
        self.script = script
        self.calls: list[list[dict]] = []
        self._lock = threading.Lock()

    def chat(self, messages, **kwargs) -> str:
        with self._lock:
            self.calls.append(messages)
            index = min(len(self.calls) - 1, len(self.script) - 1)
            decision = self.script[index]
        return json.dumps(decision, ensure_ascii=False)


class FakeTransport:
    def __init__(self) -> None:
        self.sent: list[tuple] = []

    def send_c2c_message(self, *args) -> bool:
        self.sent.append(args)
        return True

    def send_group_message(self, *args) -> bool:
        self.sent.append(args)
        return True


class BrainTestBase(unittest.TestCase):
    def make_brain(self, script, *, setup=None, snapshot=None, eat_handler=None):
        self.tmp = tempfile.mkdtemp(prefix="bunny-brain-")
        config = BrainConfig(
            paths=BrainPaths(root=self.tmp),
            state_tick_interval=0.05,
            settle_window=0.05,
            message_send_interval=0.0,
            capability_timeout_default=5.0,
            max_idle_seconds=3600,
        )
        self.llm = FakeLLM(script)
        self.transport = FakeTransport()
        self.snapshot = dict(snapshot or {"satiety": 80.0})
        brain = Brain(
            config,
            llm=self.llm,
            transport=self.transport,
            snapshot_getter=lambda: dict(self.snapshot),
            eat_handler=eat_handler,
            enable_logging=False,
        )
        if setup is not None:
            setup(brain)
        brain.start()
        self.addCleanup(self._stop, brain)
        return brain

    @staticmethod
    def _stop(brain: Brain) -> None:
        try:
            brain.stop("test")
        except Exception:
            pass

    def pump(self, brain: Brain, predicate, timeout: float = 4.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            brain.tick(0.05)
            if predicate():
                return True
            time.sleep(0.03)
        return bool(predicate())

    def wait_calls(self, brain: Brain, count: int, timeout: float = 4.0) -> bool:
        return self.pump(
            brain, lambda: len(self.llm.calls) >= count, timeout=timeout
        )


class StartupTest(BrainTestBase):
    def test_startup_and_snapshot(self):
        brain = self.make_brain(
            [{"action": "idle", "handled": [], "next_wake_in": 3600}]
        )
        self.assertTrue(self.wait_calls(brain, 1))
        snapshot = brain.snapshot()
        self.assertAlmostEqual(snapshot["satiety"], 80.0)
        self.assertEqual(snapshot["mode"], "awake")
        self.assertTrue(brain.started)
        self.assertIsNotNone(brain.memory.compressor)

    def test_shutdown_flushes(self):
        brain = self.make_brain(
            [{"action": "idle", "handled": [], "next_wake_in": 3600}]
        )
        self.assertTrue(self.wait_calls(brain, 1))
        brain.stop("done")
        self.assertFalse(brain.started)


class MessageFlowTest(BrainTestBase):
    def test_passive_reply_keeps_msg_id(self):
        brain = self.make_brain(
            [
                {"action": "idle", "handled": [], "next_wake_in": 3600},
                {
                    "action": "chat.reply",
                    "target": {"friend_id": "u1", "conversation_id": "u1"},
                    "args": {"text": "你好呀"},
                    "handled": ["ev_000001", "ev_000002"],
                    "next_wake_in": 3600,
                },
            ]
        )
        self.assertTrue(self.wait_calls(brain, 1))
        event = QQAdapter(brain).submit_c2c("u1", "m1", "你好")
        self.assertEqual(event.kind.value, "message")
        self.assertTrue(self.pump(brain, lambda: bool(self.transport.sent)))
        self.assertEqual(self.transport.sent[0][0], "u1")
        self.assertEqual(self.transport.sent[0][1], "m1")
        self.assertEqual(self.transport.sent[0][2], "你好呀")
        self.assertEqual(brain.events.unhandled_ids(), set())
        context = brain.memory.recent_context()
        self.assertIn("你好呀", context)
        self.assertIn("Alice", context)


class EatFlowTest(BrainTestBase):
    def test_eat_invokes_desktop_handler(self):
        fed: list[str] = []
        brain = self.make_brain(
            [
                {"action": "idle", "handled": [], "next_wake_in": 3600},
                {
                    "action": "eat",
                    "args": {"food": "苹果"},
                    "handled": ["ev_000001", "ev_000002"],
                    "next_wake_in": 3600,
                },
            ],
            eat_handler=lambda food: fed.append(food) or True,
        )
        self.assertTrue(self.wait_calls(brain, 1))
        QQAdapter(brain).submit_c2c("u1", "m1", "吃点东西吧")
        self.assertTrue(self.pump(brain, lambda: bool(fed)))
        self.assertEqual(fed, ["苹果"])
        self.assertEqual(brain.events.unhandled_ids(), set())


class CapabilityFlowTest(BrainTestBase):
    def test_sync_tool_triggers_next_frame(self):
        brain = self.make_brain(
            [
                {"action": "idle", "handled": [], "next_wake_in": 3600},
                {
                    "action": "tool.call",
                    "calls": [{"capability": "time.now", "args": {}}],
                    "handled": [],
                    "next_wake_in": 0,
                },
                {
                    "action": "chat.reply",
                    "target": {"friend_id": "u1", "conversation_id": "u1"},
                    "args": {"text": "完成"},
                    "handled": ["ev_000001", "ev_000002", "ev_000003"],
                    "next_wake_in": 3600,
                },
            ]
        )
        self.assertTrue(self.wait_calls(brain, 1))
        QQAdapter(brain).submit_c2c("u1", "m1", "查时间")
        self.assertTrue(self.wait_calls(brain, 3))
        kinds = [event.kind.value for event in brain.events.all()]
        self.assertIn("capability_done", kinds)
        self.assertTrue(self.transport.sent)

    def test_async_tool_triggers_next_frame(self):
        def setup(brain: Brain) -> None:
            brain.registry.register(
                Capability(
                    name="test.async",
                    description="测试异步能力",
                    schema={"type": "object", "properties": {}},
                    handler=lambda **_: _slow_value(),
                    async_mode=True,
                    timeout_seconds=5,
                )
            )

        brain = self.make_brain(
            [
                {"action": "idle", "handled": [], "next_wake_in": 3600},
                {
                    "action": "tool.call",
                    "calls": [{"capability": "test.async", "args": {}}],
                    "handled": [],
                    "next_wake_in": 0,
                },
                {
                    "action": "chat.reply",
                    "target": {"friend_id": "u1", "conversation_id": "u1"},
                    "args": {"text": "异步完成"},
                    "handled": ["ev_000001", "ev_000002", "ev_000003"],
                    "next_wake_in": 3600,
                },
            ],
            setup=setup,
        )
        self.assertTrue(self.wait_calls(brain, 1))
        QQAdapter(brain).submit_c2c("u1", "m1", "异步")
        self.assertTrue(self.wait_calls(brain, 3))
        self.assertTrue(
            any(
                event.kind.value == "capability_done"
                and event.payload.get("capability") == "test.async"
                for event in brain.events.all()
            )
        )
        self.assertTrue(self.transport.sent)


class ActivityFlowTest(BrainTestBase):
    def test_pause_resume_complete(self):
        script = [
            {"action": "idle", "handled": [], "next_wake_in": 3600},
            {
                "action": "activity.start",
                "args": {"kind": "整理", "goal": "整理测试", "steps_limit": 2},
                "handled": ["ev_000001", "ev_000002"],
                "next_wake_in": 0,
            },
            {
                "action": "tool.call",
                "calls": [{"capability": "time.now", "args": {}}],
                "handled": [],
                "next_wake_in": 0,
            },
            {
                "action": "tool.call",
                "calls": [{"capability": "time.now", "args": {}}],
                "handled": [],
                "next_wake_in": 0,
            },
            {
                "action": "activity.pause",
                "args": {
                    "note": "等吃饱",
                    "resume_when": [
                        {"type": "satiety_above", "args": {"value": 90}}
                    ],
                },
                "handled": [],
                "next_wake_in": 3600,
            },
            {
                "action": "activity.resume",
                "handled": [],
                "next_wake_in": 0,
            },
            {
                "action": "activity.complete",
                "args": {"summary": "整理完了"},
                "handled": [],
                "next_wake_in": 3600,
            },
            {"action": "idle", "handled": [], "next_wake_in": 3600},
        ]
        brain = self.make_brain(script)
        self.assertTrue(self.wait_calls(brain, 1))
        QQAdapter(brain).submit_c2c("u1", "m1", "开始活动")

        self.assertTrue(
            self.pump(brain, lambda: brain.activities.paused() is not None)
        )
        paused = brain.activities.paused()
        self.assertEqual(paused.steps_used, 2)
        self.assertIn("resume_baseline", paused.checkpoint)

        self.snapshot["satiety"] = 95.0
        self.assertTrue(
            self.wait_calls(brain, 7, timeout=5.0)
        )
        activity = brain.activities.get(paused.id)
        self.assertEqual(activity.status.value, "completed")
        brain.memory.flush(2.0)
        episodes = brain.memory.recent_episodes(86400, limit=10)
        self.assertTrue(
            any("整理测试" in episode.description for episode in episodes)
        )


class RecoveryTest(BrainTestBase):
    def test_active_activity_restored_as_paused(self):
        tmp = tempfile.mkdtemp(prefix="bunny-recover-")
        config = BrainConfig(
            paths=BrainPaths(root=tmp),
            state_tick_interval=0.05,
            settle_window=0.05,
        )
        script = [{"action": "idle", "handled": [], "next_wake_in": 3600}]
        first = Brain(
            config,
            llm=FakeLLM(script),
            enable_logging=False,
        )
        first.start()
        activity = first.activities.start("测试", "重启恢复")
        time.sleep(0.1)
        first.tick(0.05)
        first.stop("test")

        second = Brain(
            config,
            llm=FakeLLM(script),
            enable_logging=False,
        )
        second.start()
        try:
            restored = second.activities.get(activity.id)
            self.assertEqual(restored.status.value, "paused")
            self.assertTrue(restored.checkpoint.get("restored_from_active"))
        finally:
            second.stop("test")

    def test_orphan_capability_marked_unknown(self):
        tmp = tempfile.mkdtemp(prefix="bunny-orphan-")
        paths = BrainPaths(root=tmp)
        paths.ensure_directories()
        with open(paths.inflight_path, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "calls": [
                        {
                            "call_id": "frame_1_call_1",
                            "capability": "web.fetch",
                            "caller": "frame_1",
                        }
                    ]
                },
                handle,
            )
        brain = Brain(
            BrainConfig(paths=paths, settle_window=0.05),
            llm=FakeLLM(
                [{"action": "idle", "handled": [], "next_wake_in": 3600}]
            ),
            enable_logging=False,
        )
        brain.start()
        try:
            failed = [
                event
                for event in brain.events.all()
                if event.kind.value == "capability_failed"
            ]
            self.assertEqual(len(failed), 1)
            self.assertEqual(failed[0].payload.get("capability"), "web.fetch")
            with open(paths.inflight_path, "r", encoding="utf-8") as handle:
                self.assertEqual(json.load(handle).get("calls"), [])
        finally:
            brain.stop("test")


def _slow_value():
    time.sleep(0.2)
    return {"ok": True}


if __name__ == "__main__":
    unittest.main()
