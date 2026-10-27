"""把 Decision 落地为输出与状态变化；不修改 Decision，不重新判断。

从旧 ``manager/effector/effector.py`` 移植，并按新架构收缩：
- 去掉 ``eat`` / ``rest`` / ``idle``（Action 已删除，身体也不再设醒/睡二元状态），
  ``body`` 与 ``eat_handler`` 依赖一并移除；
- 新增 ``lifecycle`` 依赖：终态动作在这里排下一次唤醒（``next_wake_in`` /
  ``wake_when``），不再由 brain 在应用之后另行排布。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

from manager.activity.activity import ActivityService
from manager.capabilities.executor import CapabilityExecutor
from manager.executor.chat_sender import ChatSender
from manager.lifecycle.lifecycle import Lifecycle
from manager.shared.config import BrainConfig
from manager.shared.timeutil import now_local
from manager.shared.types import (
    Action,
    Activity,
    ActivityError,
    Alarm,
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
    alarms: list[Alarm] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


class Executor:
    def __init__(
        self,
        config: BrainConfig,
        *,
        activities: ActivityService,
        chat_sender: ChatSender,
        lifecycle: Lifecycle,
        capabilities: CapabilityExecutor | None = None,
        now_fn=now_local,
    ) -> None:
        self.config = config
        self.activities = activities
        self.chat_sender = chat_sender
        self.lifecycle = lifecycle
        self.capabilities = capabilities
        self.now_fn = now_fn
        self.last_delivery: DeliveryStatus | None = None

    # ------------------------------------------------------------------
    def apply(
        self,
        decision: Decision,
        *,
        speaker: str | None = None,
        caller: str = "",
        at: datetime | None = None,
    ) -> EffectResult:
        at = at or self.now_fn()
        result = EffectResult()
        target = decision.target

        # 1. 聊天
        if decision.action in (Action.CHAT_REPLY, Action.CHAT_REACH_OUT):
            self._chat(decision, target, result)

        # 2. 能力调用
        if decision.action is Action.TOOL_CALL:
            self._tool_call(decision, caller, result)

        # 3. 活动
        if decision.activity_update is not None:
            self._update_activity(decision, result)
        self._activity_action(decision, result)

        # 4. 排下一次唤醒（闹钟）；tool.call 由能力结果触发下一帧，不排。
        if decision.is_terminal:
            self._schedule_wake(decision, result)

        return result

    # ------------------------------------------------------------------
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

    def _schedule_wake(
        self, decision: Decision, result: EffectResult
    ) -> None:
        """终态动作把下一次唤醒写进闹钟；LLM 没给时间时只清掉旧闹钟，
        仅靠定期心跳兜底。"""
        try:
            result.alarms = self.lifecycle.schedule(
                decision.next_wake_in, decision.wake_when
            )
        except Exception as exc:
            logger.warning("排下一次唤醒失败：%s", exc)
            result.errors.append(f"闹钟排布失败: {exc}")


__all__ = ["EffectResult", "Executor"]
