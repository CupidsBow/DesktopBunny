"""召回的选择器：用一次独立的模型调用，从候选里挑真正相关的。

关键词召回只能保证"字面沾边"，挑不出"这条跟现在说的事有关"。所以分两步：

1. 关键词先粗筛出一批候选（便宜，保证召回率）；
2. 把候选的编号 + 一行摘要交给一个**独立的小调用**（不是认知帧那次），
   让它挑出最相关的几条（保证准确率）。

模型调用失败就退回关键词顺序——召回是锦上添花，不能拖垮一帧。
"""

from __future__ import annotations

import logging
from typing import Any, Mapping, Sequence

from manager.shared.jsonutil import extract_json
from manager.shared.llm_client import LLMClient

logger = logging.getLogger(__name__)

_SYSTEM = """你是 Alice 的检索员，不是 Alice 本人。
给你一条最近的对话，和一批候选旧记忆。挑出**确实相关**的几条。

规则：
1. 只挑和这条消息讲的事情真正相关的；沾一个字不算。
2. 宁缺毋滥。一条都不相关就返回空数组。
3. 只输出 JSON，不要解释。"""


def select_relevant(
    llm: LLMClient,
    query: str,
    candidates: Sequence[Mapping[str, Any]],
    limit: int,
    *,
    timeout: float | None = None,
) -> list[int] | None:
    """返回候选下标（按相关性排序，最多 ``limit`` 个）。

    Returns:
        下标列表；调用失败或输出不合法时返回 ``None``，由调用方退回关键词顺序。
    """
    if not candidates:
        return None
    lines = [
        f"{index}. {_one_line(item.get('summary'))}"
        for index, item in enumerate(candidates)
    ]
    prompt = (
        f"最近的对话：{query.strip()}\n\n"
        "候选旧记忆：\n" + "\n".join(lines) + f"\n\n最多挑 {limit} 条。"
    )
    try:
        raw = llm.chat(
            [
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": prompt},
            ],
            temperature=0.0,
            timeout=timeout,
            json_mode=True,
        )
    except Exception as exc:  # 选择失败不该影响召回本身
        logger.debug("召回选择调用失败：%s", exc)
        return None

    try:
        data = extract_json(raw)
    except ValueError:
        logger.debug("召回选择输出不可解析：%s", raw[:120])
        return None
    picks: Any = data.get("picks") if isinstance(data, Mapping) else data
    if not isinstance(picks, list):
        return None

    chosen: list[int] = []
    for item in picks:
        try:
            index = int(item)
        except (TypeError, ValueError):
            continue
        if 0 <= index < len(candidates) and index not in chosen:
            chosen.append(index)
        if len(chosen) >= limit:
            break
    return chosen


def _one_line(value: Any, limit: int = 80) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


__all__ = ["select_relevant"]
