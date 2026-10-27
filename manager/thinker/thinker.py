"""一次认知帧：调 LLM、把工具调用映射成 Decision、必要时修复一次。"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from manager.frame_generator.frame_generator import Frame
from manager.shared.config import BrainConfig
from manager.shared.llm_client import LLMClient
from manager.shared.tools_spec import ToolSpec, decision_mapping
from manager.shared.types import Decision, DecisionError, LLMError
from manager.thinker.decision import DecisionParser

logger = logging.getLogger(__name__)


@dataclass
class FrameResult:
    decision: Decision | None
    raw: str = ""
    error: str | None = None
    repaired: bool = False
    duration_ms: float = 0.0

    @property
    def ok(self) -> bool:
        return self.decision is not None


class Thinker:
    def __init__(
        self,
        config: BrainConfig,
        llm: LLMClient,
        *,
        parser: DecisionParser | None = None,
    ) -> None:
        self.config = config
        self.llm = llm
        self.parser = parser or DecisionParser(config)
        #: 认知帧统计，用于观察协议稳定性。
        self.stats = {"frames": 0, "repaired": 0, "failed": 0}

    def run(
        self,
        frame: Frame,
        *,
        inbox_ids: Iterable[str] | None = None,
    ) -> FrameResult:
        """跑一帧并记账。"""
        result = self._run(frame, inbox_ids=inbox_ids)
        self.stats["frames"] += 1
        if result.repaired:
            self.stats["repaired"] += 1
        if result.decision is None:
            self.stats["failed"] += 1
        return result

    def describe_stats(self) -> str:
        frames = self.stats["frames"]
        if not frames:
            return "尚无认知帧"
        rate = self.stats["repaired"] / frames * 100
        return (
            f"帧 {frames}｜修复 {self.stats['repaired']}（{rate:.1f}%）"
            f"｜失败 {self.stats['failed']}"
        )

    # ------------------------------------------------------------------
    def _run(
        self,
        frame: Frame,
        *,
        inbox_ids: Iterable[str] | None = None,
    ) -> FrameResult:
        """调 LLM 产出并校验 Decision；不合法时按配置修复一次。"""
        started = time.monotonic()
        try:
            message = self.llm.complete(
                frame.messages,
                tools=frame.tool_spec.tools,
                timeout=self.config.frame_timeout_seconds,
            )
        except LLMError as exc:
            return FrameResult(
                decision=None,
                error=f"LLM 调用失败：{exc}",
                duration_ms=_elapsed(started),
            )

        raw = _describe_message(message)
        try:
            decision = self._map(message, frame.tool_spec, inbox_ids)
            return FrameResult(
                decision=decision,
                raw=raw,
                duration_ms=_elapsed(started),
            )
        except (ValueError, DecisionError) as exc:
            first_error = exc
            logger.info(
                "工具调用不合法，尝试修复：%s｜原始输出：%s", exc, raw[:300]
            )
            if self.config.max_decision_repair <= 0:
                return FrameResult(
                    decision=None,
                    raw=raw,
                    error=str(exc),
                    duration_ms=_elapsed(started),
                )

        repair_messages = list(frame.messages)
        repair_messages.append({"role": "assistant", "content": raw})
        repair_messages.append(
            {"role": "user", "content": _repair_text(first_error)}
        )
        try:
            message2 = self.llm.complete(
                repair_messages,
                tools=frame.tool_spec.tools,
                timeout=self.config.frame_timeout_seconds,
            )
        except LLMError as exc:
            return FrameResult(
                decision=None,
                raw=raw,
                error=f"修复调用失败：{exc}",
                repaired=True,
                duration_ms=_elapsed(started),
            )

        raw2 = _describe_message(message2)
        try:
            decision = self._map(message2, frame.tool_spec, inbox_ids)
        except (ValueError, DecisionError) as exc2:
            logger.info(
                "修复后仍不合法：%s｜原始输出：%s", exc2, raw2[:300]
            )
            return FrameResult(
                decision=None,
                raw=raw2,
                error=f"Decision 仍不合法：{exc2}",
                repaired=True,
                duration_ms=_elapsed(started),
            )
        return FrameResult(
            decision=decision,
            raw=raw2,
            repaired=True,
            duration_ms=_elapsed(started),
        )

    def _map(
        self,
        message: Mapping[str, Any],
        spec: ToolSpec,
        inbox_ids: Iterable[str] | None,
    ) -> Decision:
        """工具调用 → Decision 字典 → 走同一套校验。"""
        mapping = decision_mapping(list(message.get("tool_calls") or []), spec)
        return self.parser.parse_mapping(mapping, inbox_ids)


def _describe_message(message: Mapping[str, Any]) -> str:
    """把一条助手消息压成一行，供日志与修复提示回放。"""
    parts: list[str] = []
    content = str(message.get("content") or "").strip()
    if content:
        parts.append(content[:500])
    for call in message.get("tool_calls") or []:
        function = call.get("function") or {}
        parts.append(f"{function.get('name')}({function.get('arguments')})")
    return " | ".join(parts) or "（空输出）"


def _repair_text(error: Exception) -> str:
    return (
        f"上一轮的调用不合法：{error}。\n"
        "请重新决定：从工具表里调用恰好一个工具，参数符合它自己的 schema。"
        "不要输出解释文字，也不要调用不存在的工具。"
    )


def _elapsed(started: float) -> float:
    return (time.monotonic() - started) * 1000


__all__ = ["Thinker", "FrameResult"]
