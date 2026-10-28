"""情境装配：把各模块的只读状态汇总成一份 JSON 报告。"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Iterable

from manager.memory.index import render_line
from manager.memory.memory import Memory
from manager.shared.timeutil import now_local, to_iso
from manager.shared.types import (
    Activity,
    BodyState,
    DeliveryStatus,
    EnvironmentState,
    Event,
    EventKind,
    SelfState,
    TurnState,
    WakeReason,
)

logger = logging.getLogger(__name__)

#: 情境里带几个好友摘要。
_FRIENDS_LIMIT = 12


class SituationBuilder:
    def __init__(
        self,
        memory: Memory,
        *,
        now_fn=now_local,
        name: str = "Alice",
    ) -> None:
        self.memory = memory
        self.now_fn = now_fn
        self.name = name

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
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """装配情境。

        Returns:
            ``(situation, related)``：情境 JSON 与自动召回结果。
            召回单独返回，因为它要作为一条独立消息发出，不埋在情境里。
        """
        now = now or self.now_fn()
        unhandled = [event for event in events if not event.is_handled]
        speaker = _current_speaker(unhandled)
        view = self.memory.view(speaker)

        primary = active_activity or paused_activity
        related = self._related(unhandled, primary)
        situation: dict[str, Any] = {
            "now": to_iso(now),
            "wake_reasons": [
                {
                    "kind": reason.kind.value,
                    "detail": reason.detail,
                    **({"event_id": reason.event_id} if reason.event_id else {}),
                }
                for reason in wake_reasons
            ],
            "self": {
                "identity": self.name,
                "mood": round(self_state.mood, 3),
                # mood_label 不再重复：它已作为 system 末尾的一行上下文。
                "urge_details": [
                    {
                        "name": urge.name,
                        "level": round(urge.level, 3),
                        "reason": urge.reason,
                        **({"target": urge.target} if urge.target else {}),
                    }
                    for urge in self_state.urges
                ],
                "attitudes": {
                    friend_id: {
                        "value": attitude.value,
                        "confidence": attitude.confidence,
                        "relation": attitude.relation.value,
                    }
                    for friend_id, attitude in self_state.attitudes.items()
                },
            },
            "body": {
                "satiety": f"{round(body.satiety)}/100",
                "energy": f"{round(body.energy, 2)}/1",
                "social_battery": f"{round(body.social_battery, 2)}/1",
                "presence": body.presence,
            },
            "environment": {
                "someone_present": environment.someone_present,
                "idle_seconds": (
                    round(environment.idle_seconds, 1)
                    if environment.idle_seconds is not None
                    else None
                ),
                "system": environment.system_state,
                "peers": [
                    {
                        "name": peer.name,
                        "slug": peer.slug,
                        # 直接给出可用地址，省得她自己拼成 peertestbot
                        "friend_id": f"peer:{peer.slug}",
                    }
                    for peer in environment.peers
                ],
            },
            "activity": _activity(primary),
            "turn": _turn(turn_state),
            "inbox": [_event(event) for event in unhandled],
            "memories": {
                "self": view.self_summary,
                "goals": view.goals_summary,
                "preferences": view.preferences_summary,
                "about_speaker": view.about_speaker,
                "speaker_first_seen": view.speaker_first_seen,
                "speaker_last_seen": view.speaker_last_seen,
                "index": [render_line(entry) for entry in view.index],
                "friends": self._friend_lines(),
            },
            "delivery": _delivery(delivery),
        }
        if notes:
            situation["notes"] = [str(note) for note in notes]
        return situation, related

    # ------------------------------------------------------------------
    def _friend_lines(self) -> list[dict[str, Any]]:
        """认识谁：按最近互动排序的好友摘要，供主动联系前回忆对方。"""
        try:
            profiles = self.memory.friends(limit=_FRIENDS_LIMIT)
        except Exception as exc:
            logger.debug("读取好友摘要失败：%s", exc)
            return []
        lines: list[dict[str, Any]] = []
        for profile in profiles:
            if not profile.friend_id:
                continue
            impression = " ".join(
                (profile.impression or profile.shared_history or "").split()
            )
            lines.append(
                {
                    "friend_id": profile.friend_id,
                    "impression": impression[:80],
                    "last_seen": profile.last_seen,
                }
            )
        return lines

    def _related(
        self, events: list[Event], activity: Activity | None
    ) -> list[dict[str, Any]]:
        """每帧自动召回：拿本帧的消息和活动目标当查询，把候选记忆放进情境。

        这原本是 ``memory.recall`` 能力，靠 LLM 自己想起来才调；现在改成装配层
        每次都做，配额度上限控制上下文。召不到就返回空列表。
        """
        texts = [
            str(event.payload.get("text") or "")
            for event in events
            if event.kind is EventKind.MESSAGE
        ]
        if activity is not None:
            texts.append(activity.goal or "")
        query = " ".join(part.strip() for part in texts if part and part.strip())
        if not query:
            return []

        config = self.memory.config
        try:
            candidates = self.memory.recall(
                query, limit=int(config.recall_auto_limit)
            )
        except Exception as exc:  # 召回失败不该拖垮整帧
            logger.debug("自动召回失败：%s", exc)
            return []

        budget = max(0, int(config.recall_auto_max_chars))
        related: list[dict[str, Any]] = []
        used = 0
        for item in candidates:
            summary = str(item.get("summary") or "").strip()
            if not summary:
                continue
            remaining = budget - used
            if remaining <= 0:
                break
            if len(summary) > remaining:
                if remaining < 40:
                    break
                summary = summary[:remaining].rstrip() + "…"
            used += len(summary)
            related.append(
                {
                    "ref": item.get("ref"),
                    "summary": summary,
                    "valence": item.get("valence"),
                }
            )
        return related


# ----------------------------------------------------------------------
def _current_speaker(events: list[Event]) -> str | None:
    for event in reversed(events):
        if event.kind is EventKind.MESSAGE and event.source.friend_id:
            return event.source.friend_id
    return None


def _activity(activity: Activity | None) -> dict[str, Any] | None:
    if activity is None:
        return None
    data: dict[str, Any] = {
        "id": activity.id,
        "status": activity.status.value,
        "kind": activity.kind,
        "goal": activity.goal,
        "progress": round(activity.progress, 3),
        "steps_used": activity.steps_used,
        "steps_left": max(0, activity.steps_limit - activity.steps_used),
        "last_result": activity.last_result,
        "state": activity.state,
        "resume_when": [
            {"type": predicate.type.value, "args": predicate.args}
            for predicate in activity.resume_when
        ],
    }
    if activity.status.value == "paused":
        data["paused"] = True
    if activity.checkpoint.get("restored_from_active"):
        data["restored"] = True
        data["restored_note"] = "上次退出时活动正在进行，已恢复为暂停"
    return data


def _turn(turn: TurnState | None) -> dict[str, Any]:
    if turn is None:
        return {"intent": None, "frames_used": 0, "pending_tool_results": []}
    return {
        "intent": turn.intent,
        "frames_used": turn.frames_used,
        "pending_tool_results": [
            result.to_dict() for result in turn.pending_tool_results
        ],
    }


def _delivery(delivery: DeliveryStatus | None) -> dict[str, Any]:
    if delivery is None:
        return {"last_ok": None, "detail": None}
    return {"last_ok": delivery.ok, "detail": delivery.detail}


def _enum_value(value: Any) -> Any:
    """枚举取 .value，字符串等原样返回。"""
    return value.value if hasattr(value, "value") else value


def _event(event: Event) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": event.id,
        "kind": _enum_value(event.kind),
        "at": event.at,
        "status": _enum_value(event.status),
    }
    if event.arrived_during is not None:
        base["arrived_during"] = {
            "type": event.arrived_during.type,
            "id": event.arrived_during.id,
        }
    if event.kind is EventKind.MESSAGE:
        base.update(
            {
                "friend_id": event.source.friend_id,
                "text": event.payload.get("text", ""),
                "msg_id": event.payload.get("msg_id"),
            }
        )
    else:
        base["payload"] = event.payload
    return base


__all__ = ["SituationBuilder"]
