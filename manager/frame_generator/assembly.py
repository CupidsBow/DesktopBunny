"""装配模型：把一帧要发的东西分层攒好，再压平成 messages。

对齐 DSH 的 ``PromptAssembly``：内容按职责分层，顺序固定。

- **sections**：人设 + 机制。稳定，改人设才变。
- **contexts**：时间、心情、较早对话摘要。每帧变，但追加在稳定段之后，
  所以稳定前缀仍然可以被缓存命中。
- **turns**：最近几轮逐字对话，真正的多轮 messages。
- **recall**：本帧自动召回，单独一条。
- **situation**：这一刻发生了什么，最后一条。

线上只有 system / user / assistant 三种角色，分层是为了"怎么攒"，
不是为了"怎么传"。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class PromptAssembly:
    """一帧的分层内容。"""

    #: 人设卡正文（{name} 已替换）。
    personality: str = ""
    #: 机制层：与具体兔子无关的通用规则。
    mechanics: str = ""
    #: 每帧变动的上下文：时间、心情、较早对话摘要。
    contexts: list[str] = field(default_factory=list)
    #: 最近几轮对话，形如 {"role": "user"|"assistant", "content": ...}
    turns: list[dict[str, str]] = field(default_factory=list)
    #: 本帧自动召回的摘要。
    recall: list[str] = field(default_factory=list)
    #: 本帧状态。
    situation: dict[str, Any] = field(default_factory=dict)

    def system_text(self) -> str:
        """sections 在前，contexts 追加在后。"""
        parts = [self.personality.strip(), self.mechanics.strip()]
        parts.extend(text.strip() for text in self.contexts if text and text.strip())
        return "\n\n".join(part for part in parts if part)

    def render(self) -> list[dict[str, str]]:
        """压平成 API 的 messages。"""
        messages: list[dict[str, str]] = [
            {"role": "system", "content": self.system_text()}
        ]
        for turn in self.turns:
            role = str(turn.get("role") or "")
            content = str(turn.get("content") or "")
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": content})
        if self.recall:
            lines = "\n".join(f"- {text}" for text in self.recall)
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "（这是系统自动翻出来的旧记忆，不是对方说的话。）\n"
                        f"你刚才想起了几件旧事（可能不完整，也不一定都相关）：\n{lines}"
                    ),
                }
            )
        messages.append(
            {
                "role": "user",
                "content": "## 当前情境\n\n```json\n"
                + json.dumps(
                    self.situation, ensure_ascii=False, indent=2, default=str
                )
                + "\n```\n\n现在，根据情境调用一个工具。",
            }
        )
        return messages


__all__ = ["PromptAssembly"]
