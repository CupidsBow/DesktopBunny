"""一次认知帧：组装 prompt、调用 LLM、解析 Decision、必要时修复一次。"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Mapping

from manager.cognition.decision import DecisionParser, repair_message
from manager.cognition.prompt import build_messages
from manager.shared.config import BrainConfig
from manager.shared.llm_client import LLMClient
from manager.shared.types import Decision, DecisionError, LLMError

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


class Cognition:
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

    def run(
        self,
        situation: Mapping[str, Any],
        capabilities: list[Mapping[str, Any]],
        *,
        inbox_ids: set[str] | None = None,
    ) -> FrameResult:
        started = time.monotonic()
        messages = build_messages(situation, capabilities)
        try:
            raw = self.llm.chat(
                messages, timeout=self.config.frame_timeout_seconds
            )
        except LLMError as exc:
            return FrameResult(
                decision=None,
                error=f"LLM 调用失败：{exc}",
                duration_ms=(time.monotonic() - started) * 1000,
            )

        try:
            decision = self.parser.parse(raw, inbox_ids)
            return FrameResult(
                decision=decision,
                raw=raw,
                duration_ms=(time.monotonic() - started) * 1000,
            )
        except DecisionError as exc:
            first_error = exc
            logger.info("Decision 解析失败，尝试修复：%s", exc)
            if self.config.max_decision_repair <= 0:
                return FrameResult(
                    decision=None,
                    raw=raw,
                    error=str(exc),
                    duration_ms=(time.monotonic() - started) * 1000,
                )

        repair_messages = list(messages)
        repair_messages.append({"role": "assistant", "content": raw})
        repair_messages.append(
            {"role": "user", "content": repair_message(raw, first_error)}
        )
        try:
            raw2 = self.llm.chat(
                repair_messages, timeout=self.config.frame_timeout_seconds
            )
        except LLMError as exc:
            return FrameResult(
                decision=None,
                raw=raw,
                error=f"修复调用失败：{exc}",
                repaired=True,
                duration_ms=(time.monotonic() - started) * 1000,
            )
        try:
            decision = self.parser.parse(raw2, inbox_ids)
        except DecisionError as exc2:
            return FrameResult(
                decision=None,
                raw=raw2,
                error=f"Decision 仍不合法：{exc2}",
                repaired=True,
                duration_ms=(time.monotonic() - started) * 1000,
            )
        return FrameResult(
            decision=decision,
            raw=raw2,
            repaired=True,
            duration_ms=(time.monotonic() - started) * 1000,
        )


__all__ = ["Cognition", "FrameResult"]
