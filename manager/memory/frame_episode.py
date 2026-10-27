"""机制侧的情景写入：一帧结束时由代码记录事实。

设计变更：情景记忆不再由 LLM 通过 ``remember`` 自愿提交，而是在**终态帧**
结束后由机制自动生成一条 L2 情景。这是一条纯函数：

- **事实**来自这一帧真正发生的事：动作、对方、说了什么、结束的活动、
  处理了哪些事件、有没有报错。
- **情绪**不在这里判断。``valence`` / ``emotion`` / ``arousal`` 由调用方从
  当帧的 ``SelfState`` 取，机制只负责把"发生了什么"写下来。

好处是不再依赖模型自觉：不会因为模型没填 ``remember`` 就丢掉一天的经历，
也不会因为模型随手编一个 valence 就把记忆带偏。
"""

from __future__ import annotations

from collections.abc import Sequence

from manager.shared.types import (
    Action,
    Activity,
    ActivityStatus,
    Event,
    EventKind,
)

#: 索引行 / frontmatter 里的一行摘要上限。
_DESCRIPTION_LIMIT = 80
#: 正文里单段引文的截断长度。
_TEXT_LIMIT = 200

_TERMINAL_ACTIVITY = (
    ActivityStatus.COMPLETED,
    ActivityStatus.ABANDONED,
    ActivityStatus.FAILED,
)

_ACTION_TEXT = {
    Action.ACTIVITY_PAUSE: ("暂停活动", "暂停了当前活动。"),
    Action.ACTIVITY_RESUME: ("继续活动", "继续了当前活动。"),
}


def compose_episode(
    decision,
    *,
    speaker: str | None = None,
    events: Sequence[Event] = (),
    activity: Activity | None = None,
    errors: Sequence[str] = (),
    trail: Sequence[str] = (),
) -> tuple[str, str] | None:
    """把一帧的结果压成一条情景；没有值得记的事时返回 ``None``。

    ``trail`` 是本回合用过的工具（``fs.read 刷钱机.txt`` 这样的短标签）。
    有它就能给"翻了一会儿东西、什么也没说"的探索留一条痕——
    否则一整轮探索会随 idle 一起蒸发。

    Returns:
        ``(description, content)``：一行摘要与正文。
    """
    handled = _handled_lines(events)
    steps = [str(step).strip() for step in trail if str(step).strip()]
    described = _describe(decision, speaker, activity)
    if described is None:
        described = _describe_silence(decision, events)
    if described is None and steps:
        described = _describe_exploration(steps)
    if described is None:
        return None
    description, content = described

    lines: list[str] = []
    if content:
        lines.append(content)
    if steps:
        lines.append("过程：" + "；".join(steps[-12:]))
    if handled:
        lines.append("当帧处理：" + "；".join(handled))
    if errors:
        lines.append("异常：" + _clip("；".join(errors), _TEXT_LIMIT))
    body = "\n".join(lines) or description

    return _clip(description, _DESCRIPTION_LIMIT), body


# ----------------------------------------------------------------------
def _describe(
    decision, speaker: str | None, activity: Activity | None
) -> tuple[str, str] | None:
    action = decision.action
    args = decision.args or {}

    if activity is not None and activity.status in _TERMINAL_ACTIVITY:
        return _describe_activity_end(activity)

    if action is Action.CHAT_REPLY:
        who = speaker or "对方"
        text = _clip(str(args.get("text") or ""), _TEXT_LIMIT)
        return f"回复 {who}：{_clip(text, 40)}", f"回复 {who}：{text}"

    if action is Action.CHAT_REACH_OUT:
        who = speaker or "朋友"
        text = _clip(str(args.get("text") or ""), _TEXT_LIMIT)
        return f"主动联系 {who}：{_clip(text, 40)}", f"主动联系 {who}：{text}"

    if action is Action.ACTIVITY_START:
        kind = str(args.get("kind") or "未命名")
        goal = _clip(str(args.get("goal") or ""), _TEXT_LIMIT)
        return f"开始 {kind}：{_clip(goal, 40)}", f"开始 {kind} 活动。\n目标：{goal}"

    if action in _ACTION_TEXT:
        short, full = _ACTION_TEXT[action]
        goal = _clip(str((activity.goal if activity else "") or ""), _TEXT_LIMIT)
        return short, f"{full}\n活动：{goal}" if goal else full

    if action is Action.IDLE:
        return None

    return None


def _describe_silence(
    decision, events: Sequence[Event]
) -> tuple[str, str] | None:
    """``idle`` 只在真的看到了消息时才留痕；纯发呆不写。"""
    if decision.action is not Action.IDLE:
        return None
    messages = [e for e in events if e.kind is EventKind.MESSAGE]
    if not messages:
        return None
    who = messages[0].source.friend_id or "对方"
    if len(messages) == 1:
        return f"没回复 {who}", f"看到了 {who} 的消息，这一帧没有回应。"
    return f"没回复 {len(messages)} 条消息", "看到了消息，这一帧没有回应。"


def _describe_exploration(steps: Sequence[str]) -> tuple[str, str]:
    """没有聊天、没有活动、只有一串工具调用：记成一次探索。"""
    head = "、".join(steps[:3])
    body = "\n".join(f"- {step}" for step in steps[-12:])
    return f"探索：{head}", f"这一轮做了 {len(steps)} 步：\n{body}"


def _describe_activity_end(activity: Activity) -> tuple[str, str]:
    status = {
        ActivityStatus.COMPLETED: "完成",
        ActivityStatus.ABANDONED: "放弃",
        ActivityStatus.FAILED: "失败",
    }[activity.status]
    goal = _clip(activity.goal or "", _TEXT_LIMIT)
    result = _clip(activity.last_result or "", _TEXT_LIMIT)
    lines = [f"{activity.kind} 活动{status}。", f"目标：{goal}"]
    if result:
        lines.append(f"结果：{result}")
    return f"{activity.kind} 活动{status}：{_clip(goal, 40)}", "\n".join(lines)


def _handled_lines(events: Sequence[Event]) -> list[str]:
    lines: list[str] = []
    for event in events:
        if event.kind is not EventKind.MESSAGE:
            continue
        who = event.source.friend_id or "对方"
        text = _clip(str(event.payload.get("text") or ""), 60)
        lines.append(f"{who}「{text}」")
    return lines


def _clip(text: str, limit: int) -> str:
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…"


__all__ = ["compose_episode"]
