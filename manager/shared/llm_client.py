"""LLM HTTP 传输：超时、有限重试、退避。

只负责把 messages 变成文本，不解析 Decision，也不拼 prompt。
"""

from __future__ import annotations

import logging
import time
from typing import Any, Mapping, Sequence

import requests

from constants import constants
from manager.shared.types import LLMError, LLMTimeoutError

logger = logging.getLogger(__name__)


class LLMClient:
    """OpenAI 兼容的 chat completions 客户端。"""

    def __init__(
        self,
        *,
        api_key: str,
        chat_url: str,
        model: str,
        retry_limit: int = 2,
        backoff_seconds: float = 1.0,
        timeout: float = 60.0,
    ) -> None:
        self.api_key = api_key
        self.chat_url = chat_url
        self.model = model
        self.retry_limit = max(0, int(retry_limit))
        self.backoff_seconds = max(0.0, float(backoff_seconds))
        self.timeout = float(timeout)

    @classmethod
    def from_constants(cls, **overrides: Any) -> "LLMClient":
        params = {
            "api_key": constants.LLM_API_KEY,
            "chat_url": constants.LLM_CHAT_URL,
            "model": constants.LLM_MODEL,
        }
        params.update(overrides)
        return cls(**params)

    def chat(
        self,
        messages: Sequence[Mapping[str, Any]],
        *,
        timeout: float | None = None,
        temperature: float | None = None,
    ) -> str:
        """调用模型并返回文本；失败抛 ``LLMError``。"""
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [dict(message) for message in messages],
        }
        if temperature is not None:
            payload["temperature"] = temperature
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        request_timeout = float(timeout or self.timeout)

        last_error: Exception | None = None
        for attempt in range(self.retry_limit + 1):
            try:
                response = requests.post(
                    self.chat_url,
                    json=payload,
                    headers=headers,
                    timeout=request_timeout,
                )
                if response.status_code >= 500 or response.status_code == 429:
                    raise LLMError(
                        f"LLM 服务暂时不可用 (HTTP {response.status_code})"
                    )
                response.raise_for_status()
                data = response.json()
                return self._extract_text(data)
            except requests.Timeout as exc:
                last_error = LLMTimeoutError(f"LLM 调用超时：{exc}")
            except requests.RequestException as exc:
                last_error = LLMError(f"LLM 请求失败：{exc}")
            except (KeyError, IndexError, TypeError, ValueError) as exc:
                last_error = LLMError(f"LLM 响应格式异常：{exc}")
            except LLMError as exc:
                last_error = exc

            if attempt < self.retry_limit:
                delay = self.backoff_seconds * (2 ** attempt)
                logger.warning(
                    "LLM 调用失败（第 %s 次），%.1fs 后重试：%s",
                    attempt + 1,
                    delay,
                    last_error,
                )
                time.sleep(delay)

        assert last_error is not None
        raise last_error

    @staticmethod
    def _extract_text(data: Mapping[str, Any]) -> str:
        message = data["choices"][0]["message"]
        content = message.get("content")
        if not content:
            reasoning = message.get("reasoning_content")
            if reasoning:
                logger.warning(
                    "LLM 返回空 content，退回 reasoning_content 兜底（%s 字）；"
                    "这一段大概率不是可解析的 Decision",
                    len(str(reasoning)),
                )
                content = reasoning
        if not content:
            raise LLMError("LLM 返回空内容")
        return str(content).strip()
