"""核心模块的轻量单元测试（不启动线程、不联网）。"""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from datetime import datetime, timedelta

from manager.activities.activities import ActivityService
from manager.activities.store import ActivityStore
from manager.capabilities.memory_api import MemoryApi
from manager.cognition.decision import DecisionError, DecisionParser
from manager.effector.message_splitter import split_message
from manager.mechanism.alarms import AlarmStore, ResumeWatcher
from manager.memory.memory import Memory
from manager.shared.config import BrainConfig, BrainPaths
from manager.shared.types import (
    Action,
    AlarmPredicate,
    BodyState,
    Decision,
    Perception,
    WakePredicateType,
)

NOW = datetime(2026, 10, 21, 12, 0, 0)


def config(root: str) -> BrainConfig:
    return BrainConfig(paths=BrainPaths(root=root))


def body(satiety: float = 80.0) -> BodyState:
    return BodyState(
        satiety=satiety,
        energy=0.7,
        alertness=0.3,
        social_battery=0.7,
        circadian=0.5,
        presence=False,
        mode="awake",
    )


def perception(present: bool = False) -> Perception:
    return Perception(now=NOW, someone_present=present)


class DecisionParserTest(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = DecisionParser(config(tempfile.mkdtemp()))

    def test_tool_call_requires_calls(self):
        with self.assertRaises(DecisionError):
            self.parser.parse('{"action": "tool.call", "calls": []}')

    def test_resume_when_only_on_pause(self):
        with self.assertRaises(DecisionError):
            self.parser.parse(
                '{"action": "idle", "args": {"resume_when": []}}'
            )

    def test_resume_when_whitelist(self):
        with self.assertRaises(DecisionError):
            self.parser.parse(
                '{"action": "activity.pause", "args": '
                '{"resume_when": [{"type": "satiety_below", "args": {"value": 20}}]}}'
            )

    def test_handled_filtered_to_inbox(self):
        decision = self.parser.parse(
            '{"action": "idle", "handled": ["ev_1", "ev_2"]}',
            inbox_ids={"ev_2"},
        )
        self.assertEqual(decision.handled, ["ev_2"])

    def test_eat_is_terminal(self):
        decision = self.parser.parse('{"action": "eat", "args": {"food": "苹果"}}')
        self.assertIs(decision.action, Action.EAT)
        self.assertTrue(decision.is_terminal)

    def test_tool_frame_has_no_alarm(self):
        decision = self.parser.parse(
            '{"action": "tool.call", "calls": [{"capability": "time.now", '
            '"args": {}}], "next_wake_in": 50, '
            '"wake_when": [{"type": "satiety_below", "args": {"value": 30}}]}'
        )
        self.assertEqual(decision.next_wake_in, 0)
        self.assertEqual(decision.wake_when, [])


class AlarmStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()
        self.cfg = config(self.tmp)
        self.store = AlarmStore(
            os.path.join(self.tmp, "alarms.json"),
            self.cfg,
            now_fn=lambda: NOW,
        )

    def test_time_alarm_fires_once(self):
        alarm = self.store.schedule_time(10, now=NOW)
        self.assertEqual(self.store.due(NOW, body(), perception()), [])
        fired = self.store.due(NOW + timedelta(seconds=11), body(), perception())
        self.assertEqual([item.id for item in fired], [alarm.id])
        self.assertEqual(self.store.pending(), [])

    def test_predicate_alarm(self):
        decision = Decision(
            action="idle",
            wake_when=[
                AlarmPredicate(
                    type=WakePredicateType.SATIETY_BELOW, args={"value": 30}
                )
            ],
        )
        self.store.schedule_decision(decision, NOW)
        self.assertEqual(self.store.due(NOW, body(50), perception()), [])
        fired = self.store.due(NOW, body(20), perception())
        self.assertEqual(len(fired), 1)

    def test_presence_baseline(self):
        decision = Decision(
            action="idle",
            wake_when=[
                AlarmPredicate(
                    type=WakePredicateType.PRESENCE_CHANGED, args={}
                )
            ],
        )
        self.store.schedule_decision(decision, NOW)
        self.assertEqual(self.store.due(NOW, body(), perception(False)), [])
        fired = self.store.due(NOW, body(), perception(True))
        self.assertEqual(len(fired), 1)


class ResumeWatcherTest(unittest.TestCase):
    def test_predicate_fires_and_clears(self):
        tmp = tempfile.mkdtemp()
        cfg = config(tmp)
        service = ActivityService(
            ActivityStore(os.path.join(tmp, "activities.json")),
            cfg,
            now_fn=lambda: NOW,
        )
        activity = service.start("测试", "恢复条件")
        service.pause(
            activity.id,
            resume_when=[
                AlarmPredicate(
                    type=WakePredicateType.SATIETY_ABOVE, args={"value": 90}
                )
            ],
        )
        paused = service.paused()
        watcher = ResumeWatcher(cfg)
        baseline = watcher.prepare_baseline(
            paused, body(80), perception(), NOW
        )
        service.set_resume_baseline(paused.id, baseline)
        self.assertEqual(
            watcher.check(service, body(85), perception(), NOW), []
        )
        fired = watcher.check(service, body(95), perception(), NOW)
        self.assertEqual(len(fired), 1)
        self.assertEqual(service.paused().resume_when, [])


class ActivityStepTest(unittest.TestCase):
    def test_step_checkpoint_only_once(self):
        tmp = tempfile.mkdtemp()
        cfg = config(tmp)
        service = ActivityService(
            ActivityStore(os.path.join(tmp, "activities.json")),
            cfg,
            now_fn=lambda: NOW,
        )
        activity = service.start("测试", "步骤", steps_limit=2)
        self.assertFalse(service.record_step(activity.id))
        self.assertTrue(service.record_step(activity.id))
        checkpoint = service.step_checkpoint(activity.id)
        self.assertIsNotNone(checkpoint)
        self.assertIsNone(service.step_checkpoint(activity.id))


class _SummaryLLM:
    def __init__(
        self,
        summary: str = "用户喜欢猫，Alice 答应了下次继续聊编程。",
        episode: str = "用户提到自己喜欢猫，并约好下次聊编程入门。",
        *,
        fail: bool = False,
    ) -> None:
        self.summary = summary
        self.episode = episode
        self.fail = fail
        self.calls = 0

    def chat(self, messages, **kwargs) -> str:
        self.calls += 1
        if self.fail:
            raise RuntimeError("offline")
        return json.dumps(
            {"summary": self.summary, "episode": self.episode},
            ensure_ascii=False,
        )


class L1CompressionTest(unittest.TestCase):
    SUMMARY = "用户喜欢猫，Alice 答应了下次继续聊编程。"

    def make_memory(self, llm) -> Memory:
        tmp = tempfile.mkdtemp()
        cfg = BrainConfig(
            paths=BrainPaths(root=tmp),
            l1_context_max_chars=400,
            l1_summary_max_chars=150,
        )
        memory = Memory(
            BrainPaths(root=tmp), cfg, llm=llm, now_fn=lambda: NOW
        )
        memory.start()
        self.addCleanup(memory.stop)
        return memory

    def fill(self, memory: Memory) -> None:
        for index in range(12):
            memory.record_turn(
                "u1",
                f"第{index}条消息：" + "今天聊了很多事情。" * 4,
                None,
                at=NOW,
            )

    def wait_for(self, predicate, timeout: float = 3.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if predicate():
                return True
            time.sleep(0.02)
        return bool(predicate())

    def test_overflow_is_compressed_and_archived(self):
        llm = _SummaryLLM(self.SUMMARY)
        memory = self.make_memory(llm)
        self.fill(memory)
        self.assertTrue(
            self.wait_for(lambda: self.SUMMARY in memory.recent_context())
        )
        context = memory.recent_context()
        self.assertIn("[较早对话摘要]", context)
        self.assertIn("第11条消息", context)
        self.assertNotIn("第0条消息", context)
        self.assertTrue(llm.calls >= 1)

        memory.flush(2.0)
        episodes = memory.recent_episodes(86400, limit=10, now=NOW)
        self.assertTrue(
            any("喜欢猫" in episode.body for episode in episodes)
        )
        self.assertTrue(
            any(
                episode.description.startswith("较早对话摘要")
                for episode in episodes
            )
        )

    def test_failure_keeps_original_turns(self):
        memory = self.make_memory(_SummaryLLM(fail=True))
        self.fill(memory)
        time.sleep(0.3)
        # 展示层有兜底截断，但缓冲区里最早的对话没有被丢弃。
        self.assertIn("第0条消息", memory._recent_turns[0])
        self.assertTrue(memory.l1_overflow())
        self.assertNotIn("[较早对话摘要]", memory.recent_context())


class L1PersistenceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tempfile.mkdtemp()
        self.paths = BrainPaths(root=self.root)

    def make_memory(self) -> Memory:
        memory = Memory(self.paths, config(self.root), now_fn=lambda: NOW)
        memory.start()
        self.addCleanup(memory.stop)
        return memory

    def test_restores_turns_and_summary_regardless_of_age(self):
        first = Memory(self.paths, config(self.root), now_fn=lambda: NOW)
        first.start()
        first.record_turn("u1", "我喜欢猫。", "记住啦。", at=NOW)
        first.stop()

        with open(self.paths.l1_path, "r", encoding="utf-8") as handle:
            saved = json.load(handle)
        self.assertEqual(saved["saved_at"], "2026-10-21T12:00:00")
        self.assertTrue(any("我喜欢猫" in line for line in saved["turns"]))

        # 一份非常旧的存档：不设关闭时间，启动时照样恢复。
        saved["summary"] = "更早以前，用户说过喜欢猫。"
        saved["saved_at"] = "2000-01-01T00:00:00+08:00"
        with open(self.paths.l1_path, "w", encoding="utf-8") as handle:
            json.dump(saved, handle, ensure_ascii=False)

        second = self.make_memory()
        context = second.recent_context()
        self.assertIn("更早以前，用户说过喜欢猫。", context)
        self.assertIn("我喜欢猫", context)
        self.assertIn("记住啦", context)
        self.assertIn("[最近对话]", context)

    def test_corrupt_file_starts_empty(self):
        os.makedirs(self.paths.state_dir, exist_ok=True)
        with open(self.paths.l1_path, "w", encoding="utf-8") as handle:
            handle.write("{not json")
        memory = self.make_memory()
        self.assertEqual(memory.recent_context(), "")


class MemoryApiTest(unittest.TestCase):
    def test_rejects_episodes_and_escape(self):
        tmp = tempfile.mkdtemp()
        memory = Memory(
            BrainPaths(root=tmp), config(tmp), now_fn=lambda: NOW
        )
        api = MemoryApi(memory)
        with self.assertRaises(ValueError):
            api.write("../outside.md", "x")
        with self.assertRaises(ValueError):
            api.write("episodes/2026-10-21-a.md", "x")
        self.assertIn(
            api._normalize("friends/u1.md"),
            {"friends/u1.md", os.path.join("friends", "u1.md")},
        )


class MessageSplitterTest(unittest.TestCase):
    def test_long_text_is_split(self):
        parts = split_message("你好。" * 400, max_chars=100, max_parts=4)
        self.assertGreater(len(parts), 1)
        self.assertLessEqual(len(parts), 4)

    def test_code_block_kept_whole(self):
        text = "```python\nprint('hi')\n```"
        self.assertEqual(split_message(text, max_chars=5, max_parts=4), [text])


if __name__ == "__main__":
    unittest.main()
