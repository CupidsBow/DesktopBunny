"""从 LLM 输出里抠出 JSON，并做平衡括号扫描。"""

from __future__ import annotations

import json
import re
from typing import Any

_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.DOTALL)


def extract_json(text: str) -> Any:
    """返回第一个可解析的 JSON 值；失败抛 ``ValueError``。

    兼容三种常见输出：纯 JSON、``json`` 代码块、夹在一段解释里的 JSON。
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("空输出")

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
