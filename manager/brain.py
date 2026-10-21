"""Brain 组合根：把机制、认知、执行器与记忆装配起来。

``main.py`` 与 ``bot/`` 只依赖这里的六个方法。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Callable

from manager.activities.activities import ActivityService
from manager.activities.store import ActivityStore
from manager.body.body import Body
from manager.body.self_state import SelfStateDeriver
from manager.capabilities.artifacts import ArtifactStore
from manager.capabilities.audit import AuditLog
from manager.capabilities.executor import CapabilityExecutor
from manager.capabilities.memory_api import MemoryApi
from manager.capabilities.registry import CapabilityRegistry
from manager.cognition.cognition import Cognition
from manager.effector.chat_sender import ChatSender
from manager.effector.effector import Effector
from manager.mechanism.alarms import AlarmStore
from manager.mechanism.clocks import StateClock
from manager.mechanism.control import Control
from manager.mechanism.events import EventLedger
from manager.mechanism.mechanism import Mechanism
from manager.mechanism.persistence import PersistenceWorker
from manager.mechanism.signals import SignalDetector
from manager.mechanism.wake import WakeController
from manager.memory.consolidation import Consolidator
from manager.memory.memory import Memory
from manager.perception.perception import PerceptionService
from manager.shared.config import BrainConfig, BrainPaths
from manager.shared.llm_client import LLMClient
from manager.shared.logging_setup import setup_logging
from manager.shared.timeutil import now_local, to_iso
from manager.shared.types import (
    CapabilityCall,
    CapabilityResult,
    Event,
    EventKind,
)

logger = logging.getLogger(__name__)


class Brain:
    def __init__(
        self,
        config: BrainConfig | None = None,
        *,
        paths: BrainPaths | str | None = None,
        llm: LLMClient | None = None,
        transport: Any = None,
        snapshot_getter: Callable[[], dict[str, Any] | None] | None = None,
        eat_handler: Callable[[str], bool] | None = None,
        qq_connected_getter: Callable[[], bool] | None = None,
        now_fn: Callable[[], datetime] = now_local,
        enable_logging: bool = True,
        log_level: int = logging.INFO,
    ) -> None:
        if config is None:
            if isinstance(paths, BrainPaths):
                brain_paths = paths
            elif paths:
                brain_paths = BrainPaths(root=str(paths))
            else:
                brain_paths = BrainPaths()
            config = BrainConfig(paths=brain_paths)
        self.config = config
        self.paths = config.paths
        self.paths.ensure_directories()
        if enable_logging:
            setup_logging(self.paths.logs_dir, log_level)

        self.now_fn = now_fn
        self._started = False
        self._mechanism: Mechanism | None = None

        if llm is None:
            llm = LLMClient.from_constants(
                retry_limit=config.llm_retry_limit,
                backoff_seconds=config.llm_retry_backoff_seconds,
                timeout=config.frame_timeout_seconds,
            )
        self.llm = llm

        self.memory = Memory(self.paths, config, llm=llm, now_fn=now_fn)
        self.activities = ActivityService(
            ActivityStore(self.paths.activities_path), config, now_fn=now_fn
        )
        self.body = Body(config, now_fn=now_fn)
        self.perception = PerceptionService(config, now_fn=now_fn)
        self.self_deriver = SelfStateDeriver(config)

        self.artifacts = ArtifactStore(self.paths.artifacts_dir)
        self.audit = AuditLog(self.paths.audit_path)
        self.memory_api = MemoryApi(self.memory)
        self.registry = CapabilityRegistry(
            config,
            artifacts=self.artifacts,
            memory=self.memory_api,
            now_fn=now_fn,
        )
        self.executor = CapabilityExecutor(
            self.registry,
            config,
            artifacts=self.artifacts,
            audit=self.audit,
            on_async_result=self._on_async_result,
            inflight_path=self.paths.inflight_path,
        )
        self.chat_sender = ChatSender(config, transport)
        self.effector = Effector(
            config,
            memory=self.memory,
            activities=self.activities,
            body=self.body,
            chat_sender=self.chat_sender,
            capabilities=self.executor,
            eat_handler=eat_handler,
            now_fn=now_fn,
        )
        self.cognition = Cognition(config, llm)
        self.consolidator = Consolidator(self.memory, llm, config, now_fn=now_fn)

        self.events = EventLedger(
            self.paths.events_path, config, now_fn=now_fn
        )
        self.persistence = PersistenceWorker()
        self.alarms = AlarmStore(
            self.paths.alarms_path,
            config,
            persistence=self.persistence,
            now_fn=now_fn,
        )
        self.signals = SignalDetector(config)
        self.clock = StateClock(config, now_fn=now_fn)
        self.wake = WakeController(config)
        self.control = Control()

        self._snapshot_getter = snapshot_getter or (lambda: None)
        self._qq_connected_getter = qq_connected_getter or (lambda: False)

        self._mechanism = Mechanism(
            config,
            perception=self.perception,
            body=self.body,
            self_deriver=self.self_deriver,
            memory=self.memory,
            activities=self.activities,
            cognition=self.cognition,
            effector=self.effector,
            capabilities=self.executor,
            registry=self.registry,
            events=self.events,
            alarms=self.alarms,
            signals=self.signals,
            clock=self.clock,
            wake=self.wake,
            control=self.control,
            consolidator=self.consolidator,
            persistence=self.persistence,
            snapshot_getter=self._snapshot_getter,
            qq_connected_getter=self._qq_connected_getter,
            now_fn=now_fn,
        )

    # ------------------------------------------------------------------
    # 对外接口
    # ------------------------------------------------------------------
    def start(self) -> None:
        if self._started:
            return
        assert self._mechanism is not None
        self._mechanism.start()
        self._started = True
        logger.info("Brain 已启动：%s", self.paths.root)

    def stop(self, reason: str = "正常关闭") -> None:
        if not self._started:
            return
        assert self._mechanism is not None
        try:
            self._mechanism.stop(reason)
        finally:
            self._started = False
        logger.info("Brain 已停止：%s", reason)

    def tick(self, delta: float = 0.0) -> None:
        if not self._started or self._mechanism is None:
            return
        self._mechanism.tick(delta)

    def submit_event(self, event: Event) -> Event:
        if not self._started or self._mechanism is None:
            logger.warning("Brain 未启动，事件被忽略：%s", event.kind)
            return event
        return self._mechanism.submit_event(event)

    def feed(self) -> None:
        """桌面喂食：推高警觉、记一次互动，并唤醒 Alice。"""
        if not self._started or self._mechanism is None:
            return
        self.body.stimulate(0.2)
        self._mechanism.inject_interaction(1)
        now = self.now_fn()
        self.submit_event(
            Event(
                id="",
                kind=EventKind.SYSTEM,
                at=to_iso(now),
                payload={
                    "event": "desktop_feed",
                    "detail": "主人喂了一根胡萝卜",
                },
            )
        )

    def snapshot(self) -> dict[str, Any]:
        if self._mechanism is None:
            return {}
        state = self._mechanism.state_snapshot()
        body = state.get("body") or {}
        activity = self.activities.active() or self.activities.paused()
        return {
            "satiety": body.get("satiety"),
            "energy": body.get("energy"),
            "alertness": body.get("alertness"),
            "social_battery": body.get("social_battery"),
            "mode": body.get("mode"),
            "qq_connected": state.get("perception", {}).get("qq_connected"),
            "activity": (
                {
                    "id": activity.id,
                    "kind": activity.kind,
                    "goal": activity.goal,
                    "status": activity.status.value,
                    "progress": activity.progress,
                }
                if activity is not None
                else None
            ),
            "last_frame_at": state.get("last_frame_at"),
        }

    # ------------------------------------------------------------------
    def _on_async_result(
        self, call: CapabilityCall, result: CapabilityResult
    ) -> None:
        mechanism = self._mechanism
        if mechanism is None or not self._started:
            logger.debug("Brain 未运行，异步能力结果被忽略：%s", call.capability)
            return
        try:
            mechanism.on_capability_result(call, result)
        except Exception:
            logger.exception("处理异步能力结果失败")

    @property
    def started(self) -> bool:
        return self._started


__all__ = ["Brain"]
