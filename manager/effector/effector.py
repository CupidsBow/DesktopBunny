"""把 Decision 落地为输出与状态变化；不修改 Decision，不重新判断。"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from manager.activities.activities import ActivityService
from manager.body.body import Body
from manager.capabilities.executor import CapabilityExecutor
from manager.effector.chat_sender import ChatSender
from manager.memory.memory import Memory
from manager.shared.config import BrainConfig
from manager.shared.timeutil import now_local
from manager.shared.types import (
    Action,
    Activity,
    ActivityError,
    CapabilityCall,
    CapabilityResult,
    Decision,
    DeliveryStatus,
    Target,
)

logger = logging.getLogger(__name__)


@dataclass
class EffectResult:
    delivery: DeliveryStatus | None = None
    activity: Activity | None = None
    capability_results: list[CapabilityResult] = field(default_factory=list)
    capability_calls: list[CapabilityCall] = field(default_factory=list)
    pending_capabilities: int = 0
    ate: str | None = None
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


class Effector:
    def __init__(
        self,
        config: BrainConfig,
        *,
        memory: Memory,
        activities: ActivityService,
        body: Body,
        chat_sender: ChatSender,
        capabilities: CapabilityExecutor | None = None,
        eat_handler: Callable[[str], bool] | None = None,
        now_fn=now_local,
    ) -> None:
        self.config = config
        self.memory = memory
        self.activities = activities
        self.body = body
        self.chat_sender = chat_sender
        self.capabilities = capabilities
        self.eat_handler = eat_handler
        self.now_fn = now_fn
        self.last_delivery: DeliveryStatus | None = None

    # ------------------------------------------------------------------
    def apply(
        self,
        decision: Decision,
        *,
        speaker: str | None = None,
        conversation: str | None = None,
        caller: str = "",
        at: datetime | None = None,
    ) -> EffectResult:
        at = at or self.now_fn()
        result = EffectResult()
        target = decision.target
        speaker = (target.friend_id if target else None) or speaker
        conversation = (
            (target.conversation_id if target else None) or conversation
        )

        # 1. 记忆写入不阻塞后续发送
        self._remember(decision, speaker, conversation, at, result)

        # 2. 聊天
        if decision.action in (Action.CHAT_REPLY, Action.CHAT_REACH_OUT):
            self._chat(decision, target, result)

        # 3. 能力调用
        if decision.action is Action.TOOL_CALL:
            self._tool_call(decision, caller, result)

        # 4. 活动
        if decision.activity_update is not None:
            self._update_activity(decision, result)
        self._activity_action(decision, result)

        # 5. 自主进食
        if decision.action is Action.EAT:
            self._eat(decision, result)

        # 6. 休息/唤醒
        if decision.action is Action.REST:
            self.body.set_mode("rest")
        elif decision.action is not Action.IDLE:
            self.body.set_mode("awake")

        return result

    # ------------------------------------------------------------------
    def _remember(
        self,
        decision: Decision,
        speaker: str | None,
        conversation: str | None,
        at: datetime,
        result: EffectResult,
    ) -> None:
        if decision.remember is None or not decision.remember.text.strip():
            return
        try:
            self.memory.remember(
                decision.remember,
                speaker=speaker,
                conversation=conversation,
                at=at,
            )
        except Exception as exc:
            logger.warning("提交记忆写入失败：%s", exc)
            result.errors.append(f"记忆写入失败: {exc}")

    def _chat(
        self,
        decision: Decision,
        target: Target | None,
        result: EffectResult,
    ) -> None:
        if target is None:
            result.errors.append(f"{decision.action.value} 缺少 target")
            return
        text = str(decision.args.get("text") or "")
        delivery = (
            self.chat_sender.reach_out(target, text)
            if decision.action is Action.CHAT_REACH_OUT
            else self.chat_sender.send(target, text)
        )
        result.delivery = delivery
        self.last_delivery = delivery
        if not delivery.ok:
            logger.warning("消息未完全送达：%s", delivery.detail)

    def _tool_call(
        self, decision: Decision, caller: str, result: EffectResult
    ) -> None:
        if self.capabilities is None:
            result.errors.append("能力执行器未配置")
            return
        for call in decision.calls:
            try:
                value = self.capabilities.submit(call, caller=caller)
            except Exception as exc:
                logger.warning("派发能力 %s 失败：%s", call.capability, exc)
                result.errors.append(f"{call.capability}: {exc}")
                continue
            if value is None:
                result.pending_capabilities += 1
            else:
                result.capability_results.append(value)
                result.capability_calls.append(call)

    def _eat(self, decision: Decision, result: EffectResult) -> None:
        """把进食请求交给桌宠主线程；饱食度以桌宠为权威。"""
        food = str(decision.args.get("food") or "胡萝卜").strip()[:40] or "胡萝卜"
        if self.eat_handler is None:
            result.errors.append("未连接桌宠身体，无法进食")
            return
        try:
            accepted = bool(self.eat_handler(food))
        except Exception as exc:
            logger.warning("提交进食请求失败：%s", exc)
            result.errors.append(f"进食失败: {exc}")
            return
        if not accepted:
            result.errors.append("桌宠没有接受进食请求")
            return
        result.ate = food
        logger.info("Alice 进食：%s", food)

    def _update_activity(
        self, decision: Decision, result: EffectResult
    ) -> None:
        try:
            activity = self.activities.update(
                decision.activity_update,
                activity_id=decision.args.get("activity_id"),
            )
            if activity is not None:
                result.activity = activity
        except Exception as exc:
            result.errors.append(f"活动更新失败: {exc}")

    def _activity_action(
        self, decision: Decision, result: EffectResult
    ) -> None:
        args = decision.args or {}
        try:
            if decision.action is Action.ACTIVITY_START:
                result.activity = self.activities.start(
                    str(args.get("kind") or ""),
                    str(args.get("goal") or ""),
                    steps_limit=int(args.get("steps_limit") or 8),
                    note=args.get("note"),
                    state=args.get("state") if isinstance(args.get("state"), dict) else None,
                )
            elif decision.action is Action.ACTIVITY_PAUSE:
                result.activity = self.activities.pause(
                    args.get("activity_id"),
                    note=args.get("note"),
                    resume_when=decision.resume_when,
                )
            elif decision.action is Action.ACTIVITY_RESUME:
                result.activity = self.activities.resume(
                    args.get("activity_id")
                )
            elif decision.action is Action.ACTIVITY_COMPLETE:
                result.activity = self.activities.complete(
                    args.get("activity_id"),
                    outcome="completed",
                    summary=args.get("summary") or args.get("note"),
                )
            elif decision.action is Action.ACTIVITY_ABANDON:
                result.activity = self.activities.abandon(
                    args.get("activity_id"),
                    outcome=str(args.get("outcome") or "abandoned"),
                    summary=args.get("summary") or args.get("note"),
                )
        except ActivityError as exc:
            result.errors.append(str(exc))
        except (TypeError, ValueError) as exc:
            result.errors.append(f"活动参数不合法: {exc}")


__all__ = ["EffectResult", "Effector"]
