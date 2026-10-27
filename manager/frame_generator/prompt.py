"""认知帧的 prompt：人设卡、机制规则、情境。

system 消息 = 人设卡（``personality.py``，可编辑）+ 本模块的机制层（固定）。
user 消息只剩情境——能力清单与输出协议改由 API 的 tools 参数约束，不再拼进文本。
"""

from __future__ import annotations

import json
from typing import Any, Mapping

from manager.frame_generator.personality import render_personality

#: 机制层：与具体兔子无关的规则。写死在这里，不随人设卡改动。
_MECHANICS = """关于选择：
- 做什么、对谁说、说什么、继续还是停下、什么时候再醒来，全部由你决定。
- 聊天不是默认动作。沉默、探索、学习、整理记忆都同样合法。
- 现在没有值得做的事时，用 idle 安静结束这一帧，别硬找话聊。
- 没有人替你安排任务。你可以自己发起一段活动，也可以随时放弃它。

关于环境与身体：
- 饱食度、精力、社交电量、心情是身体的事实，不是必须服从的开关。
- 你能感知到的只有情境里列出的东西：有没有人在、键鼠闲了多久、系统状态、在线的朋友。除此之外你没有其他信息。
- 情境里没写的事可以猜，但要说成猜。
- 你可以使用这台电脑：读写文件、运行代码、访问网络、观察系统。工具会如实返回成功或失败。
- 能力调用通常需要一帧：结果会在下一帧以事件出现。
- 聊天里不念路径、文件名、系统信息、凭据和密钥原文。

关于怎么行动：
- 每帧只能调用一个工具。工具就是你的决定，不需要额外解释。
- 能力工具（如 web_search）是用一件工具，结果下一帧回来；动作工具（如 chat_reply、idle）结束这一帧。
- 不要把工具名和参数写成文字，直接调用。

不解释内部机制，不输出分析过程。"""


def system_prompt(name: str, personality: str | None = None) -> str:
    """装配 system 消息：人设卡在前，机制层在后。

    ``personality`` 由调用方从磁盘读入；不传时使用默认人设。
    """
    body = render_personality(name) if personality is None else personality.replace(
        "{name}", name
    )
    return f"{body.strip()}\n\n{_MECHANICS}"


def build_messages(
    situation: Mapping[str, Any],
    *,
    name: str = "Alice",
    personality: str | None = None,
) -> list[dict[str, str]]:
    """装配一帧的消息。能力与协议走 tools 参数，这里只给情境。"""
    user = f"""## 当前情境

```json
{_dump(situation)}
```

现在，根据情境调用一个工具。"""
    return [
        {"role": "system", "content": system_prompt(name, personality)},
        {"role": "user", "content": user},
    ]


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


__all__ = ["build_messages", "system_prompt"]
