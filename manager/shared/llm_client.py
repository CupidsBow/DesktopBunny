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

    def chat(
        self,
        messages: Sequence[Mapping[str, Any]],
        *,
        timeout: float | None = None,
        temperature: float | None = None,
        json_mode: bool = False,
    ) -> str:
        """调用模型并返回文本；失败抛 ``LLMError``。

        ``json_mode`` 打开 ``response_format={"type": "json_object"}``：模型
        不再输出 Markdown 围栏或前言，Decision 解析的失败率会明显下降。

        但不同厂商/模型对这个字段的支持并不一致（有的直接 400）。所以第一次
        被拒后自动关掉，本次立即重发，之后的所有帧都不再带——不会每帧烧一次
        失败请求。
        """
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [dict(message) for message in messages],
        }
        if temperature is not None:
            payload["temperature"] = temperature
        use_json_mode = bool(json_mode) and self._json_mode_ok
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        request_timeout = float(timeout or self.timeout)

        last_error: Exception | None = None
        attempt = 0
        while attempt <= self.retry_limit:
            body = dict(payload)
            if use_json_mode:
                body["response_format"] = {"type": "json_object"}
            try:
                response = requests.post(
                    self.chat_url,
                    json=body,
                    headers=headers,
                    timeout=request_timeout,
                )
            except requests.Timeout as exc:
                last_error = LLMTimeoutError(f"LLM 调用超时：{exc}")
            except requests.RequestException as exc:
                last_error = LLMError(f"LLM 请求失败：{exc}")
            else:
                if (
                    response.status_code == 400
                    and use_json_mode
                    and "response_format" in (response.text or "")
                ):
                    logger.warning(
                        "该模型不支持 response_format，已关闭 JSON 模式并重发"
                    )
                    self._json_mode_ok = False
                    use_json_mode = False
                    continue
                try:
                    if response.status_code >= 500 or response.status_code == 429:
                        raise LLMError(
                            f"LLM 服务暂时不可用 (HTTP {response.status_code})"
                        )
                    response.raise_for_status()
                    return self._extract_text(response.json())
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
