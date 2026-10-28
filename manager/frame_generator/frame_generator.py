"""帧生产者：把唤醒理由与各模块只读视图聚合成一帧（情境 + prompt 消息）。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable

from manager.capabilities.registry import CapabilityRegistry
from manager.frame_generator.assembly import PromptAssembly
from manager.frame_generator.personality import (
    ensure_personality_file,
    render_personality,
)
from manager.frame_generator.prompt import MECHANICS
from manager.frame_generator.situation import SituationBuilder
from manager.memory.memory import Memory
from manager.shared.tools_spec import ToolSpec, build_tools
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
    """一次认知输入：情境报告 + 待发送的消息 + 本轮可用的工具表。"""

    situation: dict[str, Any]
    messages: list[dict[str, str]]
    tool_spec: ToolSpec


class FrameGenerator:
    def __init__(
        self,
        memory: Memory,
        registry: CapabilityRegistry,
        *,
        now_fn=now_local,
        name: str = "Alice",
        personality_path: str | None = None,
    ) -> None:
        self._situation = SituationBuilder(memory, now_fn=now_fn, name=name)
        self._memory = memory
        self._registry = registry
        #: 能力在启动时就固定了，工具表编译一次即可。
        self._tool_spec = build_tools(registry.as_dicts())
        self._name = name
        self._personality_path = personality_path
        if personality_path:
            ensure_personality_file(personality_path)

    def build(
        self,
        *,
        environment: EnvironmentState,
        body: BodyState,
        self_state: SelfState,
        events: Iterable[Event],
        wake_reasons: Iterable[WakeReason],
        active_activity: Activity | None = None,
        paused_activity: Activity | None = None,
        turn_state: TurnState | None = None,
        delivery: DeliveryStatus | None = None,
        now: datetime | None = None,
        notes: Iterable[str] | None = None,
    ) -> Frame:
        situation, related = self._situation.build(
            environment=environment,
            body=body,
            self_state=self_state,
            events=events,
            wake_reasons=wake_reasons,
            active_activity=active_activity,
            paused_activity=paused_activity,
            turn_state=turn_state,
            delivery=delivery,
            now=now,
            notes=notes,
        )
        messages = PromptAssembly(
            personality=render_personality(self._name, self._personality_path),
            mechanics=MECHANICS,
            contexts=self._contexts(now or now_fn(), self_state),
            turns=self._memory.turn_messages(),
            recall=[
                str(item.get("summary") or "").strip()
                for item in related
                if str(item.get("summary") or "").strip()
            ],
            situation=situation,
        ).render()
        return Frame(
            situation=situation,
            messages=messages,
            tool_spec=self._tool_spec,
        )

    # ------------------------------------------------------------------
    def _contexts(self, now: datetime, self_state: SelfState) -> list[str]:
        """每帧变动的上下文：时间、心情、较早对话摘要。

        放在 system 的末尾而不是情境 JSON 里——它们是"关于我自己的事实"，
        不是"别人告诉我的信息"。
        """
        weekday = "一二三四五六日"[now.weekday()]
        contexts = [
            f"现在是 {now:%Y-%m-%d %H:%M}，周{weekday}。",
            f"你现在的心情：{self_state.mood_label}。",
        ]
        summary = self._memory.l1_summary.strip()
        if summary:
            contexts.append("较早的对话摘要：\n" + summary)
        return contexts


__all__ = ["Frame", "FrameGenerator"]
