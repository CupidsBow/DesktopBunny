"""Decision 的解析、校验与范围钳制。"""

from __future__ import annotations

import logging
from typing import Any, Iterable, Mapping

from manager.shared.config import BrainConfig
from manager.shared.jsonutil import extract_json
from manager.shared.types import (
    Action,
    ActivityOutcome,
    Decision,
    DecisionError,
)

logger = logging.getLogger(__name__)

_MAX_CALLS = 5
_TEXT_ACTIONS = {Action.CHAT_REPLY, Action.CHAT_REACH_OUT}


class DecisionParser:
    def __init__(self, config: BrainConfig) -> None:
        self.config = config

    def parse(
        self, raw: str, inbox_ids: Iterable[str] | None = None
    ) -> Decision:
        try:
            data = extract_json(raw)
        except ValueError as exc:
            raise DecisionError(f"输出不是可解析的 JSON：{exc}") from exc
        if not isinstance(data, Mapping):
            raise DecisionError("Decision 必须是 JSON 对象")
        try:
            decision = Decision.from_dict(data)
        except (AttributeError, TypeError, ValueError) as exc:
            # 嵌套字段类型不对（如 remember/calls/target 是字符串）会在这里炸，
            # 必须转成 DecisionError 才能走 repair 重试，而不是直接帧失败。
            raise DecisionError(f"Decision 字段类型不合法：{exc}") from exc
        self._validate(decision)
        self._clamp(
            decision, set(inbox_ids) if inbox_ids is not None else None
        )
        return decision

    # ------------------------------------------------------------------
    def _validate(self, decision: Decision) -> None:
        action = decision.action
        args = decision.args or {}

        if action is Action.TOOL_CALL:
            if not decision.calls:
                raise DecisionError("tool.call 至少需要一个 calls 项")
            if len(decision.calls) > _MAX_CALLS:
                raise DecisionError(f"一次最多调用 {_MAX_CALLS} 个能力")
            for call in decision.calls:
                if not call.capability.strip():
                    raise DecisionError("calls 中存在空能力名")
            return

        if action in _TEXT_ACTIONS:
            if decision.target is None:
                raise DecisionError(f"{action.value} 缺少 target")
            if not decision.target.friend_id:
                raise DecisionError(f"{action.value} 的 target.friend_id 为空")
            if not str(args.get("text") or "").strip():
                raise DecisionError(f"{action.value} 的 args.text 为空")
            return

        if action is Action.ACTIVITY_START:
            if not str(args.get("kind") or "").strip():
                raise DecisionError("activity.start 缺少 args.kind")
            if not str(args.get("goal") or "").strip():
                raise DecisionError("activity.start 缺少 args.goal")
            return

        if action is Action.ACTIVITY_ABANDON:
            outcome = str(args.get("outcome") or "abandoned")
            if outcome not in (
                ActivityOutcome.COMPLETED.value,
                ActivityOutcome.ABANDONED.value,
            ):
                raise DecisionError(
                    "activity.abandon 的 outcome 只能是 completed 或 abandoned"
                )
            return

        if action is Action.ACTIVITY_PAUSE:
            # from_dict 已校验 resume_when 白名单，这里再取一次确保结构合法
            _ = decision.resume_when
            return

    def _clamp(
        self, decision: Decision, inbox_ids: set[str] | None
    ) -> None:
        if decision.next_wake_in is not None:
            decision.next_wake_in = max(
                0,
                min(int(decision.next_wake_in), self.config.next_wake_max_seconds),
            )
        if decision.handled and inbox_ids is not None:
            kept = [event_id for event_id in decision.handled if event_id in inbox_ids]
            dropped = len(decision.handled) - len(kept)
            if dropped:
                logger.debug("忽略 %s 个不存在的事件 id", dropped)
            decision.handled = kept
        if decision.activity_update is not None:
            progress = decision.activity_update.progress
            if progress is not None:
                decision.activity_update.progress = max(
                    0.0, min(1.0, float(progress))
                )
        # 终态才排闹钟；tool.call 由能力结果触发下一帧
        if decision.action is Action.TOOL_CALL:
            decision.next_wake_in = 0
            decision.wake_when = []


def repair_message(raw: str, error: Exception) -> str:
    return (
        "上一条输出无法作为 Decision 执行。\n"
        f"错误：{error}\n"
        "请只输出一个完整、合法的 JSON 对象，不要解释、不要代码块以外的东西。\n"
        "动作只能是：tool.call、chat.reply、chat.reach_out、activity.start、"
        "activity.pause、activity.resume、activity.complete、activity.abandon、"
        "idle。\n"
        f"原始输出：\n{raw[:4000]}"
    )


__all__ = ["DecisionParser", "repair_message"]
