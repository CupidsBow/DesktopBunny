"""LLM HTTP 传输：超时、有限重试、退避、工具调用。

只负责把 messages 变成一条助手消息（文本 + tool_calls），不解析 Decision，
也不拼 prompt。
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
        #: 模型是否接受 ``response_format``。首次被 400 拒绝后置 False，
        #: 之后一律不带，避免每帧都撞一次。
        self._json_mode_ok = True

    @classmethod
    def from_constants(cls, **overrides: Any) -> "LLMClient":
        params = {
            "api_key": constants.LLM_API_KEY,
            "chat_url": constants.LLM_CHAT_URL,
            "model": constants.LLM_MODEL,
        }
        params.update(overrides)
        return cls(**params)

    # ------------------------------------------------------------------
    def chat(
        self,
        messages: Sequence[Mapping[str, Any]],
        *,
        timeout: float | None = None,
        temperature: float | None = None,
        json_mode: bool = False,
    ) -> str:
        """只要文本。巩固任务这类纯 JSON 输出用它。"""
        message = self.complete(
            messages,
            timeout=timeout,
            temperature=temperature,
            json_mode=json_mode,
        )
        return _extract_text(message)

    def complete(
        self,
        messages: Sequence[Mapping[str, Any]],
        *,
        tools: Sequence[Mapping[str, Any]] | None = None,
        tool_choice: str | None = "auto",
        timeout: float | None = None,
        temperature: float | None = None,
        json_mode: bool = False,
    ) -> dict[str, Any]:
        """返回助手消息本体（``content`` 与 ``tool_calls`` 都在里面）。

        ``json_mode`` 打开 ``response_format={"type": "json_object"}``；不同模型
        支持不一致，首次被 400 拒绝后自动关掉并立即重发，之后不再带。
        """
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [dict(message) for message in messages],
        }
        if temperature is not None:
            body["temperature"] = temperature
        if tools:
            body["tools"] = [dict(tool) for tool in tools]
            if tool_choice:
                body["tool_choice"] = tool_choice

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        request_timeout = float(timeout or self.timeout)
        use_json_mode = bool(json_mode) and self._json_mode_ok

        last_error: Exception | None = None
        attempt = 0
        while attempt <= self.retry_limit:
            payload = dict(body)
            if use_json_mode:
                payload["response_format"] = {"type": "json_object"}
            try:
                response = requests.post(
                    self.chat_url,
                    json=payload,
                    headers=headers,
                    timeout=request_timeout,
                )
            except requests.Timeout as exc:
                last_error = LLMTimeoutError(f"LLM 调用超时：{exc}")
            except requests.RequestException as exc:
                last_error = LLMError(f"LLM 请求失败：{exc}")
            else:
                text = response.text or ""
                if response.status_code == 400 and use_json_mode and "response_format" in text:
                    logger.warning(
                        "该模型不支持 response_format，已关闭 JSON 模式并重发"
                    )
                    self._json_mode_ok = False
                    use_json_mode = False
                    continue
                if response.status_code == 400 and "tools" in text:
                    raise LLMError(
                        "该端点或模型不支持工具调用（tools）。"
                        f"响应：{text[:300]}"
                    )
                try:
                    if response.status_code >= 500 or response.status_code == 429:
                        raise LLMError(
                            f"LLM 服务暂时不可用 (HTTP {response.status_code})"
                        )
                    response.raise_for_status()
                    return _message_of(response.json())
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
            attempt += 1

        assert last_error is not None
        raise last_error


def _message_of(data: Mapping[str, Any]) -> dict[str, Any]:
    message = data["choices"][0]["message"]
    if not isinstance(message, Mapping):
        raise TypeError("choices[0].message 不是对象")
    return dict(message)


def _extract_text(message: Mapping[str, Any]) -> str:
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


__all__ = ["LLMClient"]
