"""工具表：把能力与动作编译成 OpenAI function calling 格式。

原来的做法是把能力 schema 拼进 user 消息，再让模型吐一段 JSON、由
``DecisionParser`` 解析。现在交给 API 层约束：模型只能从这张表里挑一个，
参数也被 schema 约束，围栏、前言、多出来的解释都不再存在。

工具分两类：

- **能力工具**：``web.search`` 之类，名字里的点换成下划线（OpenAI 工具名
  只允许 ``[a-zA-Z0-9_-]``）。调用它们等于 ``action=tool.call``。
- **动作工具**：``chat_reply`` / ``activity_start`` / ``idle`` 等，对应
  ``Action`` 枚举。调用它等于选择那个动作。

映射结果仍然拼成旧的 Decision 字典，交给 ``DecisionParser`` 走同一套校验
与钳制——协议换了，校验只有一份。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

#: 动作工具：名字 → 描述、参数与必填项。``handled`` 等公共参数由
#: :func:`_action_tool` 统一补上。
_ACTION_TOOLS: dict[str, dict[str, Any]] = {
    "chat_reply": {
        "action": "chat.reply",
        "description": "回复刚给你发消息的人。没有新来信时不要用。",
        "properties": {
            "friend_id": {"type": "string", "description": "来信事件的 friend_id"},
            "text": {"type": "string", "description": "回复内容"},
        },
        "required": ["friend_id", "text"],
    },
    "chat_reach_out": {
        "action": "chat.reach_out",
        "description": "主动给朋友发消息（对方没发新消息）。被「有人来到电脑前」唤醒想搭话时用它。",
        "properties": {
            "friend_id": {"type": "string"},
            "text": {"type": "string"},
        },
        "required": ["friend_id", "text"],
    },
    "activity_start": {
        "action": "activity.start",
        "description": "开始一段持续活动。",
        "properties": {
            "kind": {"type": "string", "description": "如 explore.web / explore.pc / learn / reflect / create"},
            "goal": {"type": "string"},
            "steps_limit": {"type": "integer", "minimum": 1},
            "note": {"type": "string"},
        },
        "required": ["kind", "goal"],
    },
    "activity": {
        "action": None,
        "description": (
            "操作当前活动。op=pause 暂停、resume 继续、complete 完成、abandon 放弃。"
        ),
        "properties": {
            "op": {
                "type": "string",
                "enum": ["pause", "resume", "complete", "abandon"],
            },
            "note": {"type": "string"},
            "resume_when": {
                "type": "array",
                "items": {"type": "object"},
                "description": '暂停时的恢复条件，如 [{"type":"satiety_above","args":{"value":60}}]',
            },
            "summary": {"type": "string"},
            "outcome": {"type": "string", "enum": ["completed", "abandoned"]},
        },
        "required": ["op"],
    },
    "idle": {
        "action": "idle",
        "description": "这一帧什么都不做。没有值得做的事时用它。",
        "properties": {},
        "required": [],
    },
}

#: 合并后的 ``activity`` 工具：op → 实际动作。
_ACTIVITY_OPS: dict[str, str] = {
    "pause": "activity.pause",
    "resume": "activity.resume",
    "complete": "activity.complete",
    "abandon": "activity.abandon",
}

#: 所有动作工具共用的公共参数。
_ACTION_META: dict[str, Any] = {
    "handled": {
        "type": "array",
        "items": {"type": "string"},
        "description": "已处理完的事件 id",
    },
    "next_wake_in": {
        "type": "integer",
        "minimum": 0,
        "maximum": 86400,
        "description": "多久后再醒（秒）",
    },
    "wake_when": {
        "type": "array",
        "items": {"type": "object"},
        "description": "唤醒条件列表，如 [{\"type\":\"presence_changed\",\"args\":{}}]",
    },
    "activity_update": {
        "type": "object",
        "properties": {
            "progress": {"type": "number"},
            "last_result": {"type": "string"},
            "note": {"type": "string"},
        },
        "description": "活动进度更新；没有当前活动时忽略",
    },
}

#: 能力工具只带 handled：工具帧不是终态，排唤醒和更新进度都轮不到它。
_CAPABILITY_META_KEYS = ("handled",)


@dataclass
class ToolSpec:
    """工具表 + 反查索引。"""

    tools: list[dict[str, Any]] = field(default_factory=list)
    #: 工具名 → ("action", Action 值) 或 ("capability", 能力名)
    kinds: dict[str, tuple[str, str]] = field(default_factory=dict)


def build_tools(capabilities: Iterable[Mapping[str, Any]]) -> ToolSpec:
    """由能力清单编译出工具表。"""
    spec = ToolSpec()
    for capability in capabilities:
        name = str(capability.get("name") or "").strip()
        if not name:
            continue
        tool_name = name.replace(".", "_")[:64]
        schema = capability.get("schema")
        schema = schema if isinstance(schema, Mapping) else {}
        properties = dict(_CAPABILITY_META)
        properties.update(_json_schema(schema))
        required = [str(item) for item in (schema.get("required") or [])]
        spec.tools.append(
            {
                "type": "function",
                "function": {
                    "name": tool_name,
                    "description": str(capability.get("description") or name),
                    "parameters": {
                        "type": "object",
                        "properties": properties,
                        "required": required,
                    },
                },
            }
        )
        spec.kinds[tool_name] = ("capability", name)

    for tool_name, definition in _ACTION_TOOLS.items():
        spec.tools.append(_action_tool(tool_name, definition))
        action = definition.get("action")
        #: ``activity`` 这类合并工具没有固定动作，留到解析时按 op 决定。
        spec.kinds[tool_name] = (
            ("activity", tool_name) if action is None else ("action", action)
        )
    return spec


def decision_mapping(
    tool_calls: list[Mapping[str, Any]], spec: ToolSpec
) -> dict[str, Any]:
    """把模型返回的 tool_calls 拼成 Decision 字典。

    规则：能力工具可以多个一起调（对应 ``tool.call``）；动作工具只能有一个，
    且不能和能力混用。

    Raises:
        ValueError: 结构不合法。调用方应把它当作 DecisionError 处理。
    """
    if not tool_calls:
        raise ValueError("没有调用任何工具：必须从工具表里选一个")

    parsed: list[tuple[str, str, dict[str, Any]]] = []
    for call in tool_calls:
        function = call.get("function") or {}
        name = str(function.get("name") or "").strip()
        if name not in spec.kinds:
            raise ValueError(f"未知工具：{name!r}")
        arguments = function.get("arguments")
        if isinstance(arguments, str):
            import json

            try:
                arguments = json.loads(arguments or "{}")
            except ValueError as exc:
                raise ValueError(f"{name} 的参数不是合法 JSON：{exc}") from exc
        if not isinstance(arguments, Mapping):
            raise ValueError(f"{name} 的参数必须是对象")
        kind, target = spec.kinds[name]
        parsed.append((kind, target, dict(arguments)))

    capabilities = [item for item in parsed if item[0] == "capability"]
    actions = [item for item in parsed if item[0] in ("action", "activity")]

    if actions and capabilities:
        raise ValueError("动作工具与能力工具不能在同一帧混用")
    if len(actions) > 1:
        raise ValueError("一帧只能选择一个动作")

    handled: list[str] = []
    activity_update: dict[str, Any] | None = None
    for _, _, arguments in parsed:
        for event_id in arguments.get("handled") or []:
            text = str(event_id).strip()
            if text and text not in handled:
                handled.append(text)
        if activity_update is None and isinstance(
            arguments.get("activity_update"), Mapping
        ):
            activity_update = dict(arguments["activity_update"])

    if capabilities:
        calls = []
        for _, name, arguments in capabilities:
            args = _strip_meta(arguments, _CAPABILITY_META_KEYS + ("activity_update",))
            calls.append({"capability": name, "args": args})
        return {
            "action": "tool.call",
            "calls": calls,
            "handled": handled,
            "activity_update": activity_update,
        }

    action_kind, action_value, arguments = actions[0]
    if action_kind == "activity":
        op = str(arguments.get("op") or "").strip()
        action_value = _ACTIVITY_OPS.get(op)
        if action_value is None:
            raise ValueError(
                "activity 的 op 只能是 pause / resume / complete / abandon"
            )
        arguments = {key: value for key, value in arguments.items() if key != "op"}

    args = _strip_meta(
        arguments,
        (
            "handled",
            "next_wake_in",
            "wake_when",
            "activity_update",
            "friend_id",
        ),
    )
    mapping: dict[str, Any] = {
        "action": action_value,
        "args": args,
        "handled": handled,
        "activity_update": activity_update,
    }
    friend_id = arguments.get("friend_id")
    if friend_id:
        mapping["target"] = {"friend_id": str(friend_id)}
    for key in ("next_wake_in", "wake_when"):
        if key in arguments and arguments[key] is not None:
            mapping[key] = arguments[key]
    return mapping


# ----------------------------------------------------------------------
#: 能力工具额外暴露的参数（取自能力自己的 schema 之外）。
_CAPABILITY_META: dict[str, Any] = {
    key: _ACTION_META[key] for key in _CAPABILITY_META_KEYS
}


def _action_tool(name: str, definition: Mapping[str, Any]) -> dict[str, Any]:
    properties = dict(definition.get("properties") or {})
    properties.update(_ACTION_META)
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": str(definition.get("description") or ""),
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": list(definition.get("required") or []),
            },
        },
    }


def _json_schema(schema: Any) -> dict[str, Any]:
    """能力的 schema 是 ``{"type":"object","properties":{...},"required":[...]}``；
    这里只取 properties，required 由 :func:`build_tools` 统一处理。"""
    if not isinstance(schema, Mapping):
        return {}
    properties = schema.get("properties")
    if isinstance(properties, Mapping):
        return dict(properties)
    return {}


def _strip_meta(arguments: Mapping[str, Any], keys: Iterable[str]) -> dict[str, Any]:
    drop = set(keys)
    return {key: value for key, value in arguments.items() if key not in drop}


__all__ = ["ToolSpec", "build_tools", "decision_mapping"]
