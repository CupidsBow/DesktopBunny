import logging
import requests
from constants import constants


class ChatError(Exception):
    """调用云端模型失败时抛出，message 为面向用户的简短错误信息。"""


class ChatManager:
    """云端大模型对话管理器，单次无记忆对话。

    复用 constants 里由 .env 读入的 LLM_MODEL / LLM_CHAT_URL / LLM_API_KEY，
    每次调用独立发起一次对话，不保留上下文。
    """

    def __init__(self):
        self.logger = logging.getLogger(__name__)

    def chat(self, msg: str, raise_on_error: bool = False) -> str:
        """发送单条消息给模型并返回回复文本。

        Args:
            msg: 用户输入的单条消息
            raise_on_error: 为 True 时失败抛 ChatError，否则返回简短错误信息字符串

        Returns:
            模型回复内容；失败时按 raise_on_error 决定抛异常或返回错误信息。
        """
        payload = {
            "model": constants.LLM_MODEL,
            "messages": [{"role": "user", "content": msg}],
        }
        headers = {
            "Authorization": f"Bearer {constants.LLM_API_KEY}",
            "Content-Type": "application/json",
        }

        try:
            resp = requests.post(
                constants.LLM_CHAT_URL,
                json=payload,
                headers=headers,
                timeout=60,
            )
            resp.raise_for_status()
        except requests.RequestException as e:
            self.logger.error(f"请求模型失败: {e}")
            if raise_on_error:
                raise ChatError(f"请求模型失败：{e}") from e
            return f"请求模型失败：{e}"

        try:
            data = resp.json()
            message = data["choices"][0]["message"]
            content = message.get("content") or ""
            # DeepSeek 推理模型可能把内容放在 reasoning_content；content 为空时兜底
            if not content:
                content = message.get("reasoning_content") or ""
            return content.strip()
        except (KeyError, IndexError, TypeError, ValueError) as e:
            self.logger.error(f"解析模型响应失败: {e}")
            if raise_on_error:
                raise ChatError(f"解析模型响应失败：{e}") from e
            return f"解析模型响应失败：{e}"
