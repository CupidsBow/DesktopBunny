"""帧生产者：把唤醒理由与各模块只读视图聚合成一帧（情境 + prompt 消息）。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable

from manager.capabilities.registry import CapabilityRegistry
from manager.frame_generator.prompt import build_messages
from manager.frame_generator.situation import SituationBuilder
from manager.memory.memory import Memory
from manager.shared.config import BrainConfig
from manager.shared.timeutil import now_local
from manager.shared.types import (
    Activity,
    BodyState,
    DeliveryStatus,
    EnvironmentState,
    Event,
    SelfState,
    TurnState,
    WakeReason,
)


@dataclass
class Frame:
    """一次认知输入：情境报告 + 待发送给 LLM 的消息。"""

    situation: dict[str, Any]
    messages: list[dict[str, str]]


class FrameGenerator:
    def __init__(
        self,
        config: BrainConfig,
        memory: Memory,
        registry: CapabilityRegistry,
        *,
        now_fn=now_local,
    ) -> None:
        self._situation = SituationBuilder(config, memory, now_fn=now_fn)
        self._registry = registry

    def build(
        self,
        *,
        environment: EnvironmentState,
        body: BodyState,
        self_state: SelfState,
        events: Iterable[Event],
        wake_reasons: Iterable[WakeReason],
        capabilities: Iterable[str] | None = None,
        active_activity: Activity | None = None,
        paused_activity: Activity | None = None,
        turn_state: TurnState | None = None,
        delivery: DeliveryStatus | None = None,
        now: datetime | None = None,
        notes: Iterable[str] | None = None,
    ) -> Frame:
        names = (
            list(capabilities)
            if capabilities is not None
            else self._registry.names()
        )
        situation = self._situation.build(
            environment=environment,
            body=body,
            self_state=self_state,
            events=events,
            capabilities=names,
            wake_reasons=wake_reasons,
            active_activity=active_activity,
            paused_activity=paused_activity,
            turn_state=turn_state,
            delivery=delivery,
            now=now,
            notes=notes,
        )
        messages = build_messages(situation, self._registry.as_dicts())
        return Frame(situation=situation, messages=messages)


__all__ = ["Frame", "FrameGenerator"]
