"""从 LLM 输出里抠出 JSON，并做平衡括号扫描。"""

from __future__ import annotations

import json
import re
from typing import Any

_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.DOTALL)

# DeepSeek 原生工具调用标记（DSML）。模型在需要调用工具时，偶尔不输出 Decision JSON，
# 而是把这些标记泄漏进 content 里，导致 JSON 解析失败。这里把 DSML 翻译成等价的 Decision。
_DSML_ACTIONS = frozenset({
    "tool.call", "chat.reply", "chat.reach_out",
    "activity.start", "activity.pause", "activity.resume",
    "activity.complete", "activity.abandon",
})
_DSML_INVOKE_RE = re.compile(
    r"<｜｜DSML｜｜\s*invoke\b([^>]*)>(.*?)</｜｜DSML｜｜\s*invoke\s*>",
    re.DOTALL,
)
_DSML_PARAM_RE = re.compile(
    r"<｜｜DSML｜｜\s*parameter\b([^>]*)>(.*?)</｜｜DSML｜｜\s*parameter\s*>",
    re.DOTALL,
)
_DSML_ATTR_RE = re.compile(r'([A-Za-z_]\w*)\s*=\s*"([^"]*)"')


def _dsml_attributes(raw: str) -> dict[str, str]:
    return {name: value for name, value in _DSML_ATTR_RE.findall(raw)}


def _dsml_value(raw: str, attrs: dict[str, str]) -> Any:
    """``string="false"`` 的值是 JSON 字面量，否则按纯文本处理。"""
    value = raw.strip()
    if attrs.get("string") != "true":
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def _dsml_to_decision(text: str) -> dict[str, Any] | None:
    """把 DSML 标记翻译成 Decision 字典；不含 DSML 或解析不出时返回 ``None``。"""
    if "｜｜DSML｜｜" not in text:
        return None

    invokes: list[tuple[str, dict[str, Any]]] = []
    for open_attrs, body in _DSML_INVOKE_RE.findall(text):
        name = _dsml_attributes(open_attrs).get("name")
        if not name:
            continue
        args: dict[str, Any] = {}
        for param_attrs, param_body in _DSML_PARAM_RE.findall(body):
            attrs = _dsml_attributes(param_attrs)
            key = attrs.get("name")
            if not key:
                continue
            args[key] = _dsml_value(param_body, attrs)
        invokes.append((name, args))

    if not invokes:
        return None

    # invoke 的名字本身就是动作时，把它当作 Decision 本体。
    for name, args in invokes:
        if name in _DSML_ACTIONS:
            return {"action": name, **args}

    # 否则每个 invoke 都是一次能力调用，统一包成 tool.call。
    return {
        "action": "tool.call",
        "calls": [
            {"capability": name, "args": args} for name, args in invokes
        ],
    }


def extract_json(text: str) -> Any:
    """返回第一个可解析的 JSON 值；失败抛 ``ValueError``。

    兼容四种常见输出：DSML 工具调用标记、纯 JSON、``json`` 代码块、
    夹在一段解释里的 JSON。
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("空输出")

    decision = _dsml_to_decision(text)
    if decision is not None:
        return decision

    try:
        return json.loads(text)
    except ValueError:
        pass

    for match in _FENCE_RE.finditer(text):
        candidate = match.group(1).strip()
        try:
            return json.loads(candidate)
        except ValueError:
            continue

    for opener, closer in (("{", "}"), ("[", "]")):
        start = _find_balanced_start(text, opener, closer)
        while start is not None:
            candidate = _scan_balanced(text, start, opener, closer)
            if candidate is not None:
                try:
                    return json.loads(candidate)
                except ValueError:
                    pass
            start = _find_balanced_start(text, opener, closer, start + 1)
    raise ValueError("输出中没有可解析的 JSON")


def _find_balanced_start(
    text: str, opener: str, closer: str, offset: int = 0
) -> int | None:
    index = text.find(opener, offset)
    return index if index >= 0 else None


def _scan_balanced(
    text: str, start: int, opener: str, closer: str
) -> str | None:
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None
