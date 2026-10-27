"""认知帧的 prompt：人设卡、机制规则、情境、能力与输出协议。

system 消息 = 人设卡（``personality.py``，可编辑）+ 本模块的机制层（固定）。
机制层只写所有兔子共用的东西：动作空间、环境与身体的读法、输出协议。
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

不解释内部机制，不输出分析过程，只输出 Decision。"""


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
    capabilities: list[Mapping[str, Any]],
    *,
    name: str = "Alice",
    personality: str | None = None,
) -> list[dict[str, str]]:
    situation_json = _dump(situation)
    capability_json = _dump(capabilities)
    user = f"""## 当前情境

```json
{situation_json}
```

## 可用能力

```json
{capability_json}
```

## 输出协议

只输出一个 JSON 对象，不要解释，不要 Markdown 代码块。一次只输出一个 Decision。

```json
{{
  "action": "tool.call | chat.reply | chat.reach_out | activity.start |
             activity.pause | activity.resume | activity.complete |
             activity.abandon | idle",
  "target": {{"friend_id": "u_123"}},
  "args": {{}},
  "calls": [{{"capability": "web.search", "args": {{"query": "..."}}}}],
  "activity_update": {{"progress": 0.4, "last_result": "...", "note": "..."}},
  "handled": ["ev_000123"],
  "next_wake_in": 30,
  "wake_when": []
}}
```

字段说明：
- `next_wake_in`：单位秒；会被限制到 0 ~ 24 小时。
- `wake_when`：唤醒条件列表，元素形如 `{{"type": "...", "args": {{...}}}}`；`type` 可用 `satiety_below`(value)、`energy_below`(value)、`presence_changed`、`at_time`(time)、`activity_checkpoint`。
- `handled`：填你已经处理完的事件 id；没填的会继续出现在下一帧。

动作说明：
- `tool.call`：调用 `calls` 里的能力，本帧不是终点；结果下一帧返回。
- `chat.reply`：回复对方刚发来的消息，`args.text` 为回复内容；`target` 的 `friend_id` 照抄来信事件的 `friend_id`。没有新来信时不要用这个动作。若 `delivery.last_ok` 为 `false`，说明上一条消息可能没送到，可考虑重发或换个说法。
- `chat.reach_out`：主动给某位朋友发消息（对方没发新消息，是你要先说），`args.text` 为内容。被"有人来到电脑前"这类信号唤醒、想搭话时，一律用它，别用 `chat.reply`。
- `activity.start`：`args.kind`、`args.goal`、可选 `steps_limit`、`note`。
- `activity.pause`：暂停当前活动；可选 `args.note` 与 `args.resume_when`（条件列表，元素形如 `{{"type": "...", "args": {{...}}}}`；`type` 可用 `satiety_above`(value)、`energy_above`(value)、`presence_changed`、`at_time`(time)）。
- `activity.resume` / `activity.complete`：继续或完成当前活动。
- `activity.abandon`：结束当前活动，`args.outcome` 为 `completed` 或 `abandoned`。
- `idle`：这一帧什么都不做，直接结束；可带 `next_wake_in` 表示多久后再看。

现在，根据情境决定下一步。"""
    return [
        {"role": "system", "content": system_prompt(name, personality)},
        {"role": "user", "content": user},
    ]


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


__all__ = ["build_messages", "system_prompt"]
