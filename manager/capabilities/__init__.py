"""能力模块：注册表与执行器，管理 Alice 当前所有可执行操作。"""

from manager.capabilities.registry import CapabilityRegistry
from manager.capabilities.executor import CapabilityExecutor
from manager.capabilities.artifacts import ArtifactStore
from manager.capabilities.audit import AuditLog

__all__ = [
    "CapabilityRegistry",
    "CapabilityExecutor",
    "ArtifactStore",
    "AuditLog",
]
