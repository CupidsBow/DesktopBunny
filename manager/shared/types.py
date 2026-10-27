"""Alice 大脑的全局数据契约。

本模块只定义数据结构、枚举、数据表与异常，不包含 I/O、线程或业务判断。
跨模块传递、持久化、进入 LLM 情境的对象都从这里取类型，避免各模块各造一套。

约定：
- 枚举一律使用 ``StrEnum``，序列化后就是文档里的字符串。
- 时间点进入 JSON 一律用带时区的 ISO8601 字符串；
  活动/闹钟的持久化时间戳用 Unix 秒（float），便于比较与恢复。
- ``to_dict()`` 负责把嵌套 dataclass / 枚举 / datetime 转成 JSON 可写结构；
  每个需要从磁盘或 LLM 还原的类都提供 ``from_dict()``。
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, StrEnum
from typing import Any

JSON = dict[str, Any]

#: 交给 LLM 的情境报告。由 ``frame_generator/situation.py`` 装配成文档里的 JSON 形状；
#: 这里用类型别名而不是 dataclass，避免把“情境长什么样”锁死在共享层。
Situation = dict[str, Any]


# ---------------------------------------------------------------------------
# 序列化基类与工具
# ---------------------------------------------------------------------------


def _jsonable(value: Any) -> Any:
    """递归转换为 json.dumps 可接受的结构。"""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            f.name: _jsonable(getattr(value, f.name))
            for f in dataclasses.fields(value)
        }
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(v) for v in value]
    return value


def _coerce_enum(enum_type: type[Enum], value: Any, default: Any = None) -> Any:
    """把字符串/枚举还原成枚举；None 时返回 default。"""
    if value is None:
        return default
    if isinstance(value, enum_type):
        return value
    return enum_type(value)


class DataModel:
    """可序列化数据类的公共基类。"""

    def to_dict(self) -> JSON:
        return _jsonable(self)

    def to_json(self, **kwargs: Any) -> str:
        kwargs.setdefault("ensure_ascii", False)
        return json.dumps(self.to_dict(), **kwargs)


# ---------------------------------------------------------------------------
# 枚举
# ---------------------------------------------------------------------------


class EventKind(StrEnum):
    MESSAGE = "message"
    CAPABILITY_DONE = "capability_done"
    CAPABILITY_FAILED = "capability_failed"
    ACTIVITY_CHECKPOINT = "activity_checkpoint"
    ALARM = "alarm"
    SIGNAL = "signal"
    SYSTEM = "system"


class EventStatus(StrEnum):
    UNHANDLED = "unhandled"
    HANDLED = "handled"


class Action(StrEnum):
    TOOL_CALL = "tool.call"
    CHAT_REPLY = "chat.reply"
    CHAT_REACH_OUT = "chat.reach_out"
    ACTIVITY_START = "activity.start"
    ACTIVITY_PAUSE = "activity.pause"
    ACTIVITY_RESUME = "activity.resume"
    ACTIVITY_COMPLETE = "activity.complete"
    ACTIVITY_ABANDON = "activity.abandon"
    IDLE = "idle"


#: 会结束当前回合的动作；``tool.call`` 不在其中。
TERMINAL_ACTIONS: frozenset[Action] = frozenset(
    {
        Action.CHAT_REPLY,
        Action.CHAT_REACH_OUT,
        Action.ACTIVITY_START,
        Action.ACTIVITY_PAUSE,
        Action.ACTIVITY_RESUME,
        Action.ACTIVITY_COMPLETE,
        Action.ACTIVITY_ABANDON,
        Action.IDLE,
    }
)


class ActivityStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    ABANDONED = "abandoned"
    FAILED = "failed"


class ActivityOutcome(StrEnum):
    """``activity.abandon`` 的 ``args.outcome``；默认 abandoned。"""

    COMPLETED = "completed"
    ABANDONED = "abandoned"


class RelationLevel(StrEnum):
    STRANGER = "陌生"
    DISTANT = "疏远"
    ACQUAINTANCE = "认识"
    FAMILIAR = "熟络"
    CLOSE = "亲近"
    INTIMATE = "亲密"


class WakeReasonKind(StrEnum):
    EVENT = "event"
    ALARM = "alarm"
    SIGNAL = "signal"
    STARTUP = "startup"
    PERIODIC = "periodic"


class AlarmKind(StrEnum):
    TIME = "time"
    PREDICATE = "predicate"


class WakePredicateType(StrEnum):
    SATIETY_BELOW = "satiety_below"
    ENERGY_BELOW = "energy_below"
    SATIETY_ABOVE = "satiety_above"
    ENERGY_ABOVE = "energy_above"
    PRESENCE_CHANGED = "presence_changed"
    AT_TIME = "at_time"
    ACTIVITY_CHECKPOINT = "activity_checkpoint"


class SignalName(StrEnum):
    SATIETY = "satiety"
    ENERGY = "energy"
    SOCIAL_BATTERY = "social_battery"
    PRESENCE = "presence"


class CapabilityErrorCode(StrEnum):
    INVALID_ARGS = "invalid_args"
    TIMEOUT = "timeout"
    CAPABILITY_ERROR = "capability_error"
    NOT_FOUND = "not_found"
    UNKNOWN = "unknown"


class MemoryWriteType(StrEnum):
    CREATE_EPISODE = "create_episode"
    UPDATE_SEMANTIC = "update_semantic"
    UPDATE_INDEX = "update_index"
    ARCHIVE = "archive"


class SemanticMemoryFile(StrEnum):
    SELF = "self.md"
    GOALS = "goals.md"
    PREFERENCES = "preferences.md"


# ---------------------------------------------------------------------------
# 静态数据表
# ---------------------------------------------------------------------------


#: 每种事件必须携带的 payload 字段（最小集合）。见 brain.md §7.1。
EVENT_PAYLOAD_REQUIRED: dict[EventKind, frozenset[str]] = {
    EventKind.MESSAGE: frozenset({"text"}),
    EventKind.CAPABILITY_DONE: frozenset(
        {"call_id", "capability", "ok", "result_summary"}
    ),
    EventKind.CAPABILITY_FAILED: frozenset(
        {"call_id", "capability", "ok", "error", "reason", "detail"}
    ),
    EventKind.ACTIVITY_CHECKPOINT: frozenset(
        {"activity_id", "kind", "progress", "steps_used", "reason"}
    ),
    EventKind.ALARM: frozenset({"alarm_id", "type", "fired_at"}),
    EventKind.SIGNAL: frozenset({"signal", "from", "to"}),
    EventKind.SYSTEM: frozenset({"event"}),
}


#: 活动类型不是封闭枚举，这里只列 v1 的默认与典型能力组合。
ACTIVITY_KINDS: dict[str, tuple[str, ...]] = {
    "explore.web": ("web.search", "web.fetch"),
    "explore.pc": ("fs.list", "fs.read", "fs.search", "pc.observe"),
    "learn": ("web.search", "web.fetch", "fs.read", "fs.write", "artifact.read"),
    "reflect": ("artifact.read", "fs.write"),
    "create": ("fs.write", "sandbox.run", "artifact.read"),
}


#: v1 条件闹钟谓词及其必填参数。见 06-lifecycle.md §3.2。
WAKE_PREDICATE_ARGS: dict[WakePredicateType, frozenset[str]] = {
    WakePredicateType.SATIETY_BELOW: frozenset({"value"}),
    WakePredicateType.ENERGY_BELOW: frozenset({"value"}),
    WakePredicateType.PRESENCE_CHANGED: frozenset(),
    WakePredicateType.AT_TIME: frozenset({"time"}),
    WakePredicateType.ACTIVITY_CHECKPOINT: frozenset({"id"}),
}


#: ``activity.pause`` 的 ``args.resume_when`` 谓词白名单。
#: 与 ``wake_when`` 分开：暂停后关心的是恢复，而不是继续下滑。
RESUME_PREDICATE_ARGS: dict[WakePredicateType, frozenset[str]] = {
    WakePredicateType.SATIETY_ABOVE: frozenset({"value"}),
    WakePredicateType.ENERGY_ABOVE: frozenset({"value"}),
    WakePredicateType.PRESENCE_CHANGED: frozenset(),
    WakePredicateType.AT_TIME: frozenset({"time"}),
}


# ---------------------------------------------------------------------------
# 事件
# ---------------------------------------------------------------------------


@dataclass
class EventSource(DataModel):
    friend_id: str | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> EventSource:
        data = data or {}
        return cls(
            friend_id=data.get("friend_id"),
        )


@dataclass
class ArrivedDuring(DataModel):
    """事件到达时 Alice 正在做什么。"""

    type: str = ""
    id: str = ""

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> ArrivedDuring | None:
        if not data:
            return None
        return cls(type=str(data.get("type", "")), id=str(data.get("id", "")))


@dataclass
class Event(DataModel):
    id: str
    kind: EventKind
    at: str
    source: EventSource = field(default_factory=EventSource)
    payload: JSON = field(default_factory=dict)
    arrived_during: ArrivedDuring | None = None
    status: EventStatus = EventStatus.UNHANDLED

    @property
    def is_handled(self) -> bool:
        return self.status is EventStatus.HANDLED

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Event:
        return cls(
            id=str(data["id"]),
            kind=_coerce_enum(EventKind, data.get("kind"), EventKind.SYSTEM),
            at=str(data.get("at", "")),
            source=EventSource.from_dict(data.get("source")),
            payload=dict(data.get("payload") or {}),
            arrived_during=ArrivedDuring.from_dict(data.get("arrived_during")),
            status=_coerce_enum(
                EventStatus, data.get("status"), EventStatus.UNHANDLED
            ),
        )


@dataclass
class WakeReason(DataModel):
    kind: WakeReasonKind
    detail: str = ""
    event_id: str | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> WakeReason:
        return cls(
            kind=_coerce_enum(WakeReasonKind, data.get("kind"), WakeReasonKind.EVENT),
            detail=str(data.get("detail", "")),
            event_id=data.get("event_id"),
        )


# ---------------------------------------------------------------------------
# 决策
# ---------------------------------------------------------------------------


@dataclass
class Target(DataModel):
    friend_id: str
    msg_id: str | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Target:
        return cls(
            friend_id=str(data.get("friend_id", "")),
            msg_id=data.get("msg_id"),
        )


@dataclass
class CapabilityCall(DataModel):
    capability: str
    args: JSON = field(default_factory=dict)
    call_id: str | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> CapabilityCall:
        return cls(
            capability=str(data.get("capability", "")),
            args=dict(data.get("args") or {}),
            call_id=data.get("call_id"),
        )


@dataclass
class ActivityUpdate(DataModel):
    progress: float | None = None
    last_result: str | None = None
    note: str | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ActivityUpdate:
        return cls(
            progress=data.get("progress"),
            last_result=data.get("last_result"),
            note=data.get("note"),
        )


@dataclass
class AlarmPredicate(DataModel):
    type: WakePredicateType
    args: JSON = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> AlarmPredicate:
        type_raw = data.get("type")
        if not type_raw:
            raise ValueError("AlarmPredicate 缺少 type")
        return cls(
            type=_coerce_enum(WakePredicateType, type_raw),
            args=dict(data.get("args") or {}),
        )


def _parse_predicates(
    raw: Any,
    allowed: Mapping[WakePredicateType, frozenset[str]] | None = None,
) -> list[AlarmPredicate]:
    """解析条件列表；``allowed`` 同时充当谓词白名单与必填参数表。"""
    raw = raw or []
    if not isinstance(raw, (list, tuple)):
        raise ValueError("条件必须是数组")
    predicates: list[AlarmPredicate] = []
    for item in raw:
        if not isinstance(item, Mapping):
            raise ValueError("条件项必须是对象")
        predicates.append(AlarmPredicate.from_dict(item))
    if allowed is None:
        return predicates
    for predicate in predicates:
        required = allowed.get(predicate.type)
        if required is None:
            raise ValueError(f"不允许的条件: {predicate.type.value}")
        missing = sorted(required - predicate.args.keys())
        if missing:
            raise ValueError(
                f"{predicate.type.value} 缺少参数: {', '.join(missing)}"
            )
    return predicates


@dataclass
class Decision(DataModel):
    action: Action
    target: Target | None = None
    args: JSON = field(default_factory=dict)
    calls: list[CapabilityCall] = field(default_factory=list)
    activity_update: ActivityUpdate | None = None
    handled: list[str] = field(default_factory=list)
    next_wake_in: int | None = None
    wake_when: list[AlarmPredicate] = field(default_factory=list)

    @property
    def is_terminal(self) -> bool:
        return self.action in TERMINAL_ACTIONS

    @property
    def resume_when(self) -> list[AlarmPredicate]:
        """``activity.pause`` 的恢复条件；其他动作为空列表。"""
        if self.action is not Action.ACTIVITY_PAUSE:
            return []
        return _parse_predicates(
            self.args.get("resume_when") or [], RESUME_PREDICATE_ARGS
        )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Decision:
        action_raw = data.get("action")
        if not action_raw:
            raise DecisionError("Decision 缺少 action")
        try:
            action = _coerce_enum(Action, action_raw)
        except ValueError as exc:
            raise DecisionError(f"未知 action: {action_raw!r}") from exc

        target_data = data.get("target")
        update_data = data.get("activity_update")
        next_wake_in = data.get("next_wake_in")

        args = dict(data.get("args") or {})
        try:
            wake_when = _parse_predicates(
                data.get("wake_when") or [], WAKE_PREDICATE_ARGS
            )
            if "resume_when" in args:
                if action is not Action.ACTIVITY_PAUSE:
                    raise ValueError("resume_when 只用于 activity.pause")
                args["resume_when"] = [
                    item.to_dict()
                    for item in _parse_predicates(
                        args["resume_when"] or [], RESUME_PREDICATE_ARGS
                    )
                ]
        except ValueError as exc:
            raise DecisionError(str(exc)) from exc

        return cls(
            action=action,
            target=Target.from_dict(target_data) if target_data else None,
            args=args,
            calls=[
                CapabilityCall.from_dict(item)
                for item in (data.get("calls") or [])
            ],
            activity_update=(
                ActivityUpdate.from_dict(update_data) if update_data else None
            ),
            handled=[str(item) for item in (data.get("handled") or [])],
            next_wake_in=int(next_wake_in) if next_wake_in is not None else None,
            wake_when=wake_when,
        )


# ---------------------------------------------------------------------------
# 感知、身体与自我状态
# ---------------------------------------------------------------------------


@dataclass
class BunnySnapshot(DataModel):
    """``bot/bridge.py`` 暴露给大脑的桌宠权威快照。"""

    satiety: float

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> BunnySnapshot:
        data = data or {}
        return cls(satiety=float(data.get("satiety", 0.0)))


@dataclass
class Peer(DataModel):
    """在线的本地兔子（presence 心跳里的一条）。"""

    name: str
    slug: str
    pid: int = 0
    started_at: str = ""
    heartbeat_at: str = ""

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Peer:
        return cls(
            name=str(data.get("name", "")),
            slug=str(data.get("slug", "")),
            pid=int(data.get("pid", 0)),
            started_at=str(data.get("started_at", "")),
            heartbeat_at=str(data.get("heartbeat_at", "")),
        )


@dataclass
class PeerMessage(DataModel):
    """收件箱文件的内容；不是事件，入站时由 brain 转成 MESSAGE 事件。"""

    msg_id: str
    from_slug: str
    from_name: str
    text: str
    sent_at: str

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> PeerMessage:
        return cls(
            msg_id=str(data.get("msg_id", "")),
            from_slug=str(data.get("from_slug", "")),
            from_name=str(data.get("from_name", "")),
            text=str(data.get("text", "")),
            sent_at=str(data.get("sent_at", "")),
        )


@dataclass
class EnvironmentState(DataModel):
    now: datetime
    idle_seconds: float | None = None
    someone_present: bool | None = None
    system_state: JSON = field(default_factory=dict)
    peers: list[Peer] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> EnvironmentState:
        now = data.get("now")
        if not isinstance(now, datetime):
            now = datetime.fromisoformat(str(now))
        return cls(
            now=now,
            idle_seconds=data.get("idle_seconds"),
            someone_present=data.get("someone_present"),
            system_state=dict(data.get("system_state") or {}),
            peers=[Peer.from_dict(item) for item in (data.get("peers") or [])],
        )


@dataclass
class BodyState(DataModel):
    satiety: float
    energy: float
    social_battery: float
    presence: bool

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> BodyState:
        return cls(
            satiety=float(data.get("satiety", 0.0)),
            energy=float(data.get("energy", 0.0)),
            social_battery=float(data.get("social_battery", 0.0)),
            presence=bool(data.get("presence", False)),
        )


@dataclass
class Urge(DataModel):
    name: str
    level: float
    reason: str = ""
    target: str | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Urge:
        return cls(
            name=str(data.get("name", "")),
            level=float(data.get("level", 0.0)),
            reason=str(data.get("reason", "")),
            target=data.get("target"),
        )


@dataclass
class Attitude(DataModel):
    friend_id: str
    value: float
    confidence: float
    relation: RelationLevel = RelationLevel.STRANGER

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Attitude:
        return cls(
            friend_id=str(data.get("friend_id", "")),
            value=float(data.get("value", 0.0)),
            confidence=float(data.get("confidence", 0.0)),
            relation=_coerce_enum(
                RelationLevel, data.get("relation"), RelationLevel.STRANGER
            ),
        )


@dataclass
class SelfState(DataModel):
    mood: float
    mood_label: str
    urges: list[Urge] = field(default_factory=list)
    attitudes: dict[str, Attitude] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SelfState:
        return cls(
            mood=float(data.get("mood", 0.0)),
            mood_label=str(data.get("mood_label", "平静")),
            urges=[
                Urge.from_dict(item) for item in (data.get("urges") or [])
            ],
            attitudes={
                str(key): Attitude.from_dict(value)
                for key, value in (data.get("attitudes") or {}).items()
            },
        )


# ---------------------------------------------------------------------------
# 活动
# ---------------------------------------------------------------------------


@dataclass
class Activity(DataModel):
    id: str
    kind: str
    goal: str
    state: JSON = field(default_factory=dict)
    created_at: float = 0.0
    updated_at: float = 0.0
    steps_used: int = 0
    steps_limit: int = 8
    status: ActivityStatus = ActivityStatus.ACTIVE
    checkpoint: JSON = field(default_factory=dict)
    last_result: str = ""
    resume_when: list[AlarmPredicate] = field(default_factory=list)
    consecutive_failures: int = 0

    @property
    def progress(self) -> float:
        return float(self.state.get("progress", 0.0))

    @property
    def is_open(self) -> bool:
        return self.status in (ActivityStatus.ACTIVE, ActivityStatus.PAUSED)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Activity:
        return cls(
            id=str(data.get("id", "")),
            kind=str(data.get("kind", "")),
            goal=str(data.get("goal", "")),
            state=dict(data.get("state") or {}),
            created_at=float(data.get("created_at", 0.0)),
            updated_at=float(data.get("updated_at", 0.0)),
            steps_used=int(data.get("steps_used", 0)),
            steps_limit=int(data.get("steps_limit", 8)),
            status=_coerce_enum(
                ActivityStatus, data.get("status"), ActivityStatus.ACTIVE
            ),
            checkpoint=dict(data.get("checkpoint") or {}),
            last_result=str(data.get("last_result", "")),
            resume_when=_parse_predicates(
                data.get("resume_when") or [], RESUME_PREDICATE_ARGS
            ),
            consecutive_failures=int(data.get("consecutive_failures", 0)),
        )


@dataclass
class ActivityCheckpoint(DataModel):
    activity_id: str
    kind: str
    progress: float
    steps_used: int
    reason: str = "step_completed"

    def to_payload(self) -> JSON:
        return self.to_dict()


@dataclass
class TurnState(DataModel):
    intent: str | None = None
    frames_used: int = 0
    pending_tool_results: list[CapabilityResult] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> TurnState:
        return cls(
            intent=data.get("intent"),
            frames_used=int(data.get("frames_used", 0)),
            pending_tool_results=[
                CapabilityResult.from_dict(item)
                for item in (data.get("pending_tool_results") or [])
            ],
        )


# ---------------------------------------------------------------------------
# 能力
# ---------------------------------------------------------------------------


@dataclass
class Capability:
    """注册表里的一项能力。``handler`` 不参与序列化。"""

    name: str
    description: str
    schema: JSON
    handler: Callable[..., Any]
    async_mode: bool = False
    timeout_seconds: float = 30.0
    audit: bool = True

    def to_dict(self) -> JSON:
        """给 prompt 使用的公开描述：只含决策所需字段，不含 handler 与机制元数据。"""
        return {
            "name": self.name,
            "description": self.description,
            "schema": self.schema,
        }


@dataclass
class CapabilityResult(DataModel):
    ok: bool
    result: Any = None
    result_ref: str | None = None
    truncated: bool = False
    error: str | None = None
    reason: str | None = None
    detail: JSON = field(default_factory=dict)

    @classmethod
    def success(
        cls,
        result: Any = None,
        *,
        result_ref: str | None = None,
        truncated: bool = False,
    ) -> CapabilityResult:
        return cls(
            ok=True,
            result=result,
            result_ref=result_ref,
            truncated=truncated,
        )

    @classmethod
    def failure(
        cls,
        error: CapabilityErrorCode | str,
        reason: str,
        detail: Mapping[str, Any] | None = None,
    ) -> CapabilityResult:
        return cls(
            ok=False,
            error=str(error),
            reason=reason,
            detail=dict(detail or {}),
        )

    def to_dict(self) -> JSON:
        """按 04-capabilities.md 的成功/失败信封输出，不带无关空字段。"""
        if self.ok:
            payload: JSON = {
                "ok": True,
                "result": _jsonable(self.result),
                "truncated": self.truncated,
            }
            if self.result_ref is not None:
                payload["result_ref"] = self.result_ref
            return payload
        return {
            "ok": False,
            "error": self.error,
            "reason": self.reason,
            "detail": _jsonable(self.detail),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> CapabilityResult:
        return cls(
            ok=bool(data.get("ok", False)),
            result=data.get("result"),
            result_ref=data.get("result_ref"),
            truncated=bool(data.get("truncated", False)),
            error=data.get("error"),
            reason=data.get("reason"),
            detail=dict(data.get("detail") or {}),
        )


# ---------------------------------------------------------------------------
# 出站与记忆
# ---------------------------------------------------------------------------


@dataclass
class AuditRecord(DataModel):
    """``audit.jsonl`` 的一行。"""

    time: str
    capability: str
    args_summary: str
    ok: bool
    duration_ms: float
    caller: str = ""

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> AuditRecord:
        return cls(
            time=str(data.get("time", "")),
            capability=str(data.get("capability", "")),
            args_summary=str(data.get("args_summary", "")),
            ok=bool(data.get("ok", False)),
            duration_ms=float(data.get("duration_ms", 0.0)),
            caller=str(data.get("caller", "")),
        )


@dataclass
class DeliveryStatus(DataModel):
    ok: bool
    parts_sent: int = 0
    detail: str | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> DeliveryStatus:
        return cls(
            ok=bool(data.get("ok", False)),
            parts_sent=int(data.get("parts_sent", 0)),
            detail=data.get("detail"),
        )


@dataclass
class MemoryMetadata(DataModel):
    speaker: str | None = None
    time: str | None = None
    valence: float = 0.0
    emotion: str = "平静"
    arousal: float = 0.0

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> MemoryMetadata:
        data = data or {}
        return cls(
            speaker=data.get("speaker"),
            time=data.get("time"),
            valence=float(data.get("valence", 0.0)),
            emotion=str(data.get("emotion", "平静")),
            arousal=float(data.get("arousal", 0.0)),
        )


@dataclass
class MemoryWriteOp(DataModel):
    op: MemoryWriteType
    target: str
    content: str = ""
    metadata: MemoryMetadata = field(default_factory=MemoryMetadata)
    description: str = ""

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> MemoryWriteOp:
        op_raw = data.get("op")
        if not op_raw:
            raise ValueError("MemoryWriteOp 缺少 op")
        return cls(
            op=_coerce_enum(MemoryWriteType, op_raw),
            target=str(data.get("target", "")),
            content=str(data.get("content", "")),
            metadata=MemoryMetadata.from_dict(data.get("metadata")),
            description=str(data.get("description", "")),
        )


@dataclass
class MemoryIndexEntry(DataModel):
    """``MEMORY.md`` 的一行。"""

    name: str
    path: str
    description: str
    valence: float | None = None
    arousal: float | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> MemoryIndexEntry:
        return cls(
            name=str(data.get("name", "")),
            path=str(data.get("path", "")),
            description=str(data.get("description", "")),
            valence=data.get("valence"),
            arousal=data.get("arousal"),
        )


@dataclass
class MemoryView(DataModel):
    """记忆模块交给情境/自我状态的只读视图；不含写能力。"""

    self_summary: str = ""
    goals_summary: str = ""
    preferences_summary: str = ""
    about_speaker: str = ""
    speaker_first_seen: str | None = None
    speaker_last_seen: str | None = None
    index: list[MemoryIndexEntry] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> MemoryView:
        return cls(
            self_summary=str(data.get("self_summary", "")),
            goals_summary=str(data.get("goals_summary", "")),
            preferences_summary=str(data.get("preferences_summary", "")),
            about_speaker=str(data.get("about_speaker", "")),
            speaker_first_seen=data.get("speaker_first_seen"),
            speaker_last_seen=data.get("speaker_last_seen"),
            index=[
                MemoryIndexEntry.from_dict(item)
                for item in (data.get("index") or [])
            ],
        )


@dataclass
class Episode(DataModel):
    name: str
    description: str
    metadata: MemoryMetadata = field(default_factory=MemoryMetadata)
    body: str = ""
    path: str = ""

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Episode:
        return cls(
            name=str(data.get("name", "")),
            description=str(data.get("description", "")),
            metadata=MemoryMetadata.from_dict(data.get("metadata")),
            body=str(data.get("body", "")),
            path=str(data.get("path", "")),
        )


@dataclass
class FriendProfile(DataModel):
    """``friends/<id>.md`` 的语义内容；关系等级不落盘。"""

    friend_id: str
    description: str = ""
    first_seen: str | None = None
    last_seen: str | None = None
    impression: str = ""
    shared_history: str = ""

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> FriendProfile:
        return cls(
            friend_id=str(data.get("friend_id", "")),
            description=str(data.get("description", "")),
            first_seen=data.get("first_seen"),
            last_seen=data.get("last_seen"),
            impression=str(data.get("impression", "")),
            shared_history=str(data.get("shared_history", "")),
        )


# ---------------------------------------------------------------------------
# 闹钟与信号
# ---------------------------------------------------------------------------


@dataclass
class Alarm(DataModel):
    id: str
    kind: AlarmKind
    created_at: float
    fire_at: float | None = None
    predicate: AlarmPredicate | None = None
    source: str = "llm"
    note: str = ""

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Alarm:
        predicate_data = data.get("predicate")
        fire_at = data.get("fire_at")
        kind_raw = data.get("kind")
        if kind_raw is None:
            kind_raw = "predicate" if predicate_data else "time"
        return cls(
            id=str(data.get("id", "")),
            kind=_coerce_enum(AlarmKind, kind_raw, AlarmKind.TIME),
            created_at=float(data.get("created_at", 0.0)),
            fire_at=float(fire_at) if fire_at is not None else None,
            predicate=(
                AlarmPredicate.from_dict(predicate_data)
                if predicate_data
                else None
            ),
            source=str(data.get("source", "llm")),
            note=str(data.get("note", "")),
        )


@dataclass
class SignalChange(DataModel):
    signal: SignalName | str
    from_value: Any = None
    to_value: Any = None
    detail: str = ""

    def to_payload(self) -> JSON:
        return {
            "signal": str(self.signal),
            "from": _jsonable(self.from_value),
            "to": _jsonable(self.to_value),
            "detail": self.detail,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SignalChange:
        return cls(
            signal=str(data.get("signal", "")),
            from_value=data.get("from"),
            to_value=data.get("to"),
            detail=str(data.get("detail", "")),
        )


# ---------------------------------------------------------------------------
# 异常
# ---------------------------------------------------------------------------


class BrainError(Exception):
    """Alice 大脑的基类异常。"""


class ConfigError(BrainError):
    """配置缺失或非法。"""


class LLMError(BrainError):
    """LLM 调用失败。"""


class LLMTimeoutError(LLMError):
    """LLM 调用超时。"""


class DecisionError(BrainError):
    """Decision 无法解析或校验失败。"""


class CapabilityError(BrainError):
    """能力执行失败。"""


class CapabilityValidationError(CapabilityError):
    """能力参数不合法。"""


class CapabilityTimeoutError(CapabilityError):
    """能力执行超时。"""


class MemoryError(BrainError):
    """记忆读写失败。"""


class ActivityError(BrainError):
    """活动状态非法。"""


class PersistenceError(BrainError):
    """落盘或恢复失败。"""


__all__ = [
    "JSON",
    "Situation",
    "DataModel",
    "EventKind",
    "EventStatus",
    "Action",
    "TERMINAL_ACTIONS",
    "ActivityStatus",
    "ActivityOutcome",
    "RelationLevel",
    "WakeReasonKind",
    "AlarmKind",
    "WakePredicateType",
    "SignalName",
    "CapabilityErrorCode",
    "MemoryWriteType",
    "SemanticMemoryFile",
    "EVENT_PAYLOAD_REQUIRED",
    "ACTIVITY_KINDS",
    "WAKE_PREDICATE_ARGS",
    "RESUME_PREDICATE_ARGS",
    "EventSource",
    "ArrivedDuring",
    "Event",
    "WakeReason",
    "Target",
    "CapabilityCall",
    "ActivityUpdate",
    "AlarmPredicate",
    "Decision",
    "BunnySnapshot",
    "Peer",
    "PeerMessage",
    "EnvironmentState",
    "BodyState",
    "Urge",
    "Attitude",
    "SelfState",
    "Activity",
    "ActivityCheckpoint",
    "TurnState",
    "Capability",
    "CapabilityResult",
    "AuditRecord",
    "DeliveryStatus",
    "MemoryMetadata",
    "MemoryWriteOp",
    "MemoryIndexEntry",
    "MemoryView",
    "Episode",
    "FriendProfile",
    "Alarm",
    "SignalChange",
    "BrainError",
    "ConfigError",
    "LLMError",
    "LLMTimeoutError",
    "DecisionError",
    "CapabilityError",
    "CapabilityValidationError",
    "CapabilityTimeoutError",
    "MemoryError",
    "ActivityError",
    "PersistenceError",
]
