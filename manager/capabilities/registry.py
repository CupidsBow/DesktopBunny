"""能力注册表：名称、描述、schema、执行函数、超时与同步/异步标记。"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Callable, Mapping

from manager.capabilities.artifacts import ArtifactStore
from manager.capabilities.memory_api import MemoryApi
from manager.shared.config import BrainConfig
from manager.shared.timeutil import now_local
from manager.shared.types import Capability
from tools.fs_tools import list_dir, read_file, search_files, write_file
from tools.pc_tools import observe
from tools.sandbox_tools import run_command, run_python
from tools.web_tools import fetch_page, search_web

logger = logging.getLogger(__name__)

_OBJECT = {"type": "object"}


class CapabilityRegistry:
    def __init__(
        self,
        config: BrainConfig,
        *,
        artifacts: ArtifactStore | None = None,
        memory: MemoryApi | None = None,
        now_fn: Callable[[], datetime] = now_local,
    ) -> None:
        self.config = config
        self.artifacts = artifacts
        self.memory = memory
        self.now_fn = now_fn
        self._capabilities: dict[str, Capability] = {}
        self._register_builtins()

    # ------------------------------------------------------------------
    def register(self, capability: Capability) -> None:
        if capability.name in self._capabilities:
            logger.warning("能力 %s 已存在，将被覆盖", capability.name)
        self._capabilities[capability.name] = capability

    def add(
        self,
        name: str,
        description: str,
        schema: Mapping[str, Any],
        handler: Callable[..., Any],
        *,
        async_mode: bool = False,
        timeout_seconds: float | None = None,
        audit: bool = True,
    ) -> Capability:
        capability = Capability(
            name=name,
            description=description,
            schema=dict(schema),
            handler=handler,
            async_mode=async_mode,
            timeout_seconds=(
                timeout_seconds
                if timeout_seconds is not None
                else self.config.capability_timeout_default
            ),
            audit=audit,
        )
        self.register(capability)
        return capability

    def get(self, name: str) -> Capability | None:
        return self._capabilities.get(name)

    def names(self) -> list[str]:
        return sorted(self._capabilities)

    def all(self) -> list[Capability]:
        return [self._capabilities[name] for name in self.names()]

    def as_dicts(self) -> list[dict[str, Any]]:
        return [capability.to_dict() for capability in self.all()]

    def describe(self) -> str:
        return "\n".join(
            f"- {capability.name}: {capability.description}"
            for capability in self.all()
        )

    # ------------------------------------------------------------------
    def _register_builtins(self) -> None:
        self.add(
            "time.now",
            "读取本机当前日期、时间与星期。",
            {**_OBJECT, "properties": {}},
            self._time_now,
        )

        self.add(
            "web.search",
            "搜索网页，返回标题、链接和摘要。慢能力。",
            {
                **_OBJECT,
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 10},
                },
                "required": ["query"],
            },
            search_web,
            async_mode=True,
            timeout_seconds=30,
        )
        self.add(
            "web.fetch",
            "抓取一个网页并抽取正文。慢能力。",
            {
                **_OBJECT,
                "properties": {
                    "url": {"type": "string"},
                    "max_chars": {"type": "integer", "minimum": 500},
                },
                "required": ["url"],
            },
            fetch_page,
            async_mode=True,
            timeout_seconds=30,
        )

        self.add(
            "fs.list",
            "列出一个目录的内容。",
            {
                **_OBJECT,
                "properties": {
                    "path": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1},
                },
            },
            list_dir,
            timeout_seconds=10,
        )
        self.add(
            "fs.read",
            "读取文本文件；大文件可分 offset 读取。慢能力。",
            {
                **_OBJECT,
                "properties": {
                    "path": {"type": "string"},
                    "offset": {"type": "integer", "minimum": 0},
                    "limit": {"type": "integer", "minimum": 1},
                },
                "required": ["path"],
            },
            _ReadFile(self.config.fs_max_read_bytes),
            async_mode=True,
            timeout_seconds=20,
        )
        self.add(
            "fs.search",
            "按 glob 或正则检索文件名。慢能力。",
            {
                **_OBJECT,
                "properties": {
                    "path": {"type": "string"},
                    "pattern": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1},
                },
                "required": ["pattern"],
            },
            search_files,
            async_mode=True,
            timeout_seconds=30,
        )
        self.add(
            "fs.write",
            "写入或追加文件；会修改磁盘。",
            {
                **_OBJECT,
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                    "append": {"type": "boolean"},
                },
                "required": ["path", "content"],
            },
            write_file,
            timeout_seconds=10,
        )

        if self.artifacts is not None:
            self.add(
                "artifact.read",
                "读取大结果 artifact，按字符偏移分段。",
                {
                    **_OBJECT,
                    "properties": {
                        "ref": {"type": "string"},
                        "offset": {"type": "integer", "minimum": 0},
                        "limit": {"type": "integer", "minimum": 1},
                    },
                    "required": ["ref"],
                },
                self.artifacts.read,
                timeout_seconds=5,
            )

        self.add(
            "sandbox.run",
            "运行命令或 Python 代码；会执行任意代码。慢能力。",
            {
                **_OBJECT,
                "properties": {
                    "command": {"type": "string"},
                    "code": {"type": "string"},
                    "cwd": {"type": "string"},
                    "env": {"type": "object"},
                    "timeout": {"type": "number", "minimum": 0.5, "maximum": 600},
                },
            },
            _sandbox_run,
            async_mode=True,
            timeout_seconds=120,
        )

        self.add(
            "pc.observe",
            "观察前台窗口、键鼠空闲、进程、CPU、内存与网络。",
            {
                **_OBJECT,
                "properties": {
                    "what": {
                        "oneOf": [
                            {"type": "string"},
                            {"type": "array", "items": {"type": "string"}},
                        ]
                    }
                },
            },
            observe,
            timeout_seconds=15,
        )

        if self.memory is not None:
            self.add(
                "memory.recall",
                "按关键词召回相关记忆候选。",
                {
                    **_OBJECT,
                    "properties": {
                        "query": {"type": "string"},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 20},
                    },
                    "required": ["query"],
                },
                self.memory.recall,
                timeout_seconds=5,
            )
            self.add(
                "memory.read",
                "读取 self/goals/preferences/friends 语义记忆。",
                {
                    **_OBJECT,
                    "properties": {"target": {"type": "string"}},
                    "required": ["target"],
                },
                self.memory.read,
                timeout_seconds=5,
            )
            self.add(
                "memory.write",
                "覆盖写入语义记忆文件；会长期保留。",
                {
                    **_OBJECT,
                    "properties": {
                        "target": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["target", "content"],
                },
                self.memory.write,
                timeout_seconds=5,
            )

    def _time_now(self) -> dict[str, Any]:
        now = self.now_fn()
        return {
            "iso": now.isoformat(timespec="seconds"),
            "date": f"{now:%Y-%m-%d}",
            "time": f"{now:%H:%M:%S}",
            "weekday": ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][
                now.weekday()
            ],
            "timezone": now.tzname(),
            "timestamp": int(now.timestamp()),
        }


class _ReadFile:
    """把配置里的读取上限绑进 fs.read，但保留同名关键字参数。"""

    def __init__(self, max_bytes: int) -> None:
        self.max_bytes = max_bytes

    def __call__(
        self,
        path: str,
        offset: int = 0,
        limit: int | None = None,
    ) -> dict[str, Any]:
        return read_file(path, offset=offset, limit=limit, max_bytes=self.max_bytes)


def _sandbox_run(
    command: str | None = None,
    code: str | None = None,
    cwd: str | None = None,
    env: Mapping[str, str] | None = None,
    timeout: float = 30,
) -> dict[str, Any]:
    if bool(command) == bool(code):
        raise ValueError("command 与 code 必须且只能提供一个")
    if command:
        result = run_command(command, cwd=cwd, env=dict(env or {}), timeout=timeout)
        result["mode"] = "command"
    else:
        result = run_python(code or "", cwd=cwd, env=dict(env or {}), timeout=timeout)
        result["mode"] = "code"
    return result


__all__ = ["CapabilityRegistry"]
