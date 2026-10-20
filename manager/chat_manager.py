import json
import logging
import requests
import sympy

from constants import constants
from tools.get_local_time import get_local_time
from tools.tool_executor import ToolExecutor

MAX_TOOL_ROUNDS = 5


class ChatError(Exception):
    """调用云端模型失败时抛出，message 为面向用户的简短错误信息。"""


class ChatManager:
    """云端大模型对话管理器，单次无记忆对话。

    复用 constants 里由 .env 读入的 LLM_MODEL / LLM_CHAT_URL / LLM_API_KEY，
    每次调用独立发起一次对话，不保留上下文。
    """

    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.tools = ToolExecutor()
        self.register_tool()

    def register_tool(self):
        self.tools.registerTool(
            "get_local_time",
            "一个获取当前时间的工具，返回格式为 %Y-%m-%d %H:%M:%S",
            get_local_time,
            parameters={"type": "object", "properties": {}},
        )
        self.tools.registerTool(
            "calc",
            "计算单个表达式的值并返回结果",
            lambda expr: str(sympy.sympify(expr)),
            parameters={
                "type": "object",
                "properties": {
                    "expr": {
                        "type": "string",
                        "description": "要计算的数学表达式，例如 '1+2*3'",
                    }
                },
                "required": ["expr"],
            },
        )

    def chat(self, msg: str, raise_on_error: bool = False) -> str:
        """发送单条消息给模型并返回回复文本。

        带工具调用循环：模型返回 tool_calls 时执行工具并把结果以 role=tool 回填，
        直到模型给出最终文本答案（最多 MAX_TOOL_ROUNDS 轮）。

        Args:
            msg: 用户输入的单条消息
            raise_on_error: 为 True 时失败抛 ChatError，否则返回简短错误信息字符串

        Returns:
            模型回复内容；失败时按 raise_on_error 决定抛异常或返回错误信息。
        """
        messages = [
            {"role": "system", "content": constants.ALICE_SYSTEM_PROMPT},
            {"role": "user", "content": msg},
        ]
        tools = self.tools.getToolsSchema()
        headers = {
            "Authorization": f"Bearer {constants.LLM_API_KEY}",
            "Content-Type": "application/json",
        }

        for _ in range(MAX_TOOL_ROUNDS):
            payload = {"model": constants.LLM_MODEL, "messages": messages}
            if tools:
                payload["tools"] = tools

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
                message = resp.json()["choices"][0]["message"]
            except (KeyError, IndexError, TypeError, ValueError) as e:
                self.logger.error(f"解析模型响应失败: {e}")
                if raise_on_error:
                    raise ChatError(f"解析模型响应失败：{e}") from e
                return f"解析模型响应失败：{e}"

            tool_calls = message.get("tool_calls")
            if not tool_calls:
                content = message.get("content") or ""
                # DeepSeek 推理模型可能把内容放在 reasoning_content；content 为空时兜底
                if not content:
                    content = message.get("reasoning_content") or ""
                return content.strip()

            # 把 assistant 的 tool_calls 消息放回上下文
            assistant_msg = {"role": "assistant", "tool_calls": tool_calls}
            if message.get("content") is not None:
                assistant_msg["content"] = message["content"]
            messages.append(assistant_msg)

            # 逐个执行工具，结果以 role=tool 回填
            for tc in tool_calls:
                name = tc["function"]["name"]
                try:
                    arguments = json.loads(tc["function"].get("arguments") or "{}")
                except (ValueError, TypeError):
                    arguments = {}
                try:
                    result = self.tools.execute(name, arguments)
                    result_str = str(result)
                except Exception as e:
                    result_str = f"工具执行失败：{e}"
                    self.logger.error(f"工具 {name} 执行失败: {e}")
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id"),
                    "content": result_str,
                })

        return "（工具调用轮次超限）"
