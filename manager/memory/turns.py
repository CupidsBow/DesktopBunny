"""L1 对话条目的渲染。

``memory`` 与 ``compression`` 都用它，所以单独放一个模块，避免两边互相 import。
"""

from __future__ import annotations

from typing import Any, Mapping


def render_l1_turn(turn: Mapping[str, Any]) -> str:
    """把一条 L1 对话渲染成一行文本：``[HH:MM] 谁: 说了什么``。"""
    stamp = str(turn.get("at") or "")
    text = str(turn.get("text") or "")
    if turn.get("role") == "assistant":
        who = "Alice"
    else:
        who = str(turn.get("speaker") or "对方")
    return f"[{stamp}] {who}: {text}" if stamp else f"{who}: {text}"


__all__ = ["render_l1_turn"]
