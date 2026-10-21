"""日志配置：文件轮转 + 控制台。"""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler

_FORMAT = "%(asctime)s %(levelname)s [%(threadName)s] %(name)s: %(message)s"


def setup_logging(logs_dir: str, level: int = logging.INFO) -> logging.Logger:
    """配置根 logger；重复调用不会叠加 handler。"""
    os.makedirs(logs_dir, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(level)
    if getattr(root, "_alice_configured", False):
        return logging.getLogger("alice")

    formatter = logging.Formatter(_FORMAT, datefmt="%Y-%m-%d %H:%M:%S")
    file_handler = RotatingFileHandler(
        os.path.join(logs_dir, "brain.log"),
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    if not any(
        isinstance(handler, logging.StreamHandler)
        and not isinstance(handler, RotatingFileHandler)
        for handler in root.handlers
    ):
        console = logging.StreamHandler()
        console.setFormatter(formatter)
        root.addHandler(console)

    root._alice_configured = True  # type: ignore[attr-defined]
    return logging.getLogger("alice")
