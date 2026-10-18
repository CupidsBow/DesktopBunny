from typing import Any
import logging

class ToolExecutor:
    """
    一个工具执行器，负责管理和执行工具。
    """
    def __init__(self):
        self.tools: dict[str, dict[str, Any]] = {}
        self.logger = logging.getLogger(__name__)

    def registerTool(self, name: str, description: str, func: callable, parameters: dict[str, Any] = None):
        """
        向工具箱中注册一个新工具。

        parameters 为 OpenAI function-calling 格式的 JSON Schema，
        用于告诉模型该工具接受哪些参数；为 None 表示无参工具。
        """
        if name in self.tools:
            self.logger.warning(f"工具 '{name}' 已存在, 将被覆盖")
        self.tools[name] = {"description": description, "func": func, "parameters": parameters}
        self.logger.info(f"工具 '{name}' 已注册")

    def getTool(self, name: str) -> callable:
        """
        根据名称获取一个工具的执行函数。
        """
        return self.tools.get(name, {}).get("func")

    def execute(self, name: str, arguments: dict[str, Any] = None):
        """
        执行指定工具，arguments 为模型给出的参数 dict。
        """
        func = self.getTool(name)
        if func is None:
            raise ValueError(f"未知工具: {name}")
        arguments = arguments or {}
        if not isinstance(arguments, dict):
            raise TypeError(f"工具参数必须是 dict，收到 {type(arguments).__name__}")
        return func(**arguments)

    def getToolsSchema(self) -> list:
        """
        转成 OpenAI function-calling 的 tools 参数格式。
        """
        result = []
        for name, info in self.tools.items():
            function = {"name": name, "description": info["description"]}
            if info.get("parameters") is not None:
                function["parameters"] = info["parameters"]
            result.append({"type": "function", "function": function})
        return result

    def getAvailableTools(self) -> str:
        """
        获取所有可用工具的格式化描述字符串。
        """
        return "\n".join([
            f"- {name}: {info['description']}"
            for name, info in self.tools.items()
        ])
