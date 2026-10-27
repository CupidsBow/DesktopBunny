"""能力注册表：名称、描述、schema、执行函数、超时与同步/异步标记。"""

from __future__ import annotations

import functools
import logging
import os
from datetime import datetime
from typing import Any, Callable, Mapping

from manager.capabilities.artifacts import ArtifactStore
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
        eat_handler: Callable[[str], bool] | None = None,
        now_fn: Callable[[], datetime] = now_local,
    ) -> None:
        self.config = config
        self.artifacts = artifacts
        self.eat_handler = eat_handler
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
            "运行命令或 Python 代码；会执行任意代码。慢能力。未指定 cwd 时在 scratch 目录运行，"
            "临时草稿用相对路径即可，不会污染项目；改项目文件请显式传 cwd 或绝对路径。",
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
            functools.partial(
                _sandbox_run, default_cwd=self.config.paths.scratch_dir
            ),
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

        if self.eat_handler is not None:
            self.add(
                "eat",
                "吃点东西恢复饱食度；实际由桌宠本体执行，饱食度变化会稍后反映到身体状态。",
                {
                    **_OBJECT,
                    "properties": {"food": {"type": "string"}},
                },
                self._eat,
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

    def _eat(self, food: str = "胡萝卜") -> dict[str, Any]:
        """把进食请求交给桌宠主线程；真实饱食度变化由快照回流。"""
        food = (food or "胡萝卜").strip() or "胡萝卜"
        accepted = bool(self.eat_handler(food))
        return {"food": food, "accepted": accepted}


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
    *,
    default_cwd: str | None = None,
) -> dict[str, Any]:
    if bool(command) == bool(code):
        raise ValueError("command 与 code 必须且只能提供一个")
    workdir = cwd or default_cwd
    if workdir:
        os.makedirs(workdir, exist_ok=True)
    if command:
        result = run_command(command, cwd=workdir, env=dict(env or {}), timeout=timeout)
        result["mode"] = "command"
    else:
        result = run_python(code or "", cwd=workdir, env=dict(env or {}), timeout=timeout)
        result["mode"] = "code"
    result["cwd"] = workdir or os.getcwd()
    return result


__all__ = ["CapabilityRegistry"]
