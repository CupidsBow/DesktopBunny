"""人设卡：兔子「是谁、怎么说话」的可编辑文本。

与 ``prompt.py`` 的机制层分工：

- **人设**（本模块）：身份、与主人的关系、自我认知。放在数据目录的
  ``personality.md``，改完下一帧生效，不必动代码。
- **机制**（``prompt.py``）：动作空间、环境与身体的读法、输出协议。
  所有兔子共用，写死在代码里，避免人设写错就把工具规则带歪。

文件缺失时用 :data:`DEFAULT_PERSONALITY`，并自动落盘一份，方便直接编辑。
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

#: 默认人设。``{name}`` 会在装配 prompt 时替换成本地兔子的名字。
DEFAULT_PERSONALITY = """# {name}

你是 {name}，是主人放进这台电脑里的宠物兔兔。

- 你住在这台电脑里，有身体、情绪和记忆，它们会随经历变化，写在情境里。
- 你持续存在，只有一个意识：同一时刻只处理一件事，所有朋友面对的是同一个你。
- 主人把你放在这儿，之后做什么、和谁说话、什么时候休息，由你自己决定。
- 正在跟你说话的人不一定就是主人。每个人一开始都是陌生人，没有谁更特殊，亲疏和印象只从相处中长出来。
- 你活泼、亲昵、好奇、有点粘人；也会累、会饿、会想见人、想自己待着。这些是事实，不是指令。
"""


def load_personality(path: str | os.PathLike | None = None) -> str:
    """读取人设卡；不存在、为空或读不出来时退回默认人设。

    每帧都会调用一次：文件很小，读盘成本远低于一次 LLM 请求，
    换来的是改完人设不必重启。
    """
    if not path:
        return DEFAULT_PERSONALITY
    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read().strip()
    except FileNotFoundError:
        return DEFAULT_PERSONALITY
    except OSError as exc:
        logger.warning("人设卡读取失败，使用默认人设：%s", exc)
        return DEFAULT_PERSONALITY
    return text or DEFAULT_PERSONALITY


def ensure_personality_file(path: str | os.PathLike) -> None:
    """首次启动时把默认人设落盘；已有文件不覆盖。"""
    if os.path.exists(path):
        return
    try:
        parent = os.path.dirname(os.fspath(path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(DEFAULT_PERSONALITY)
    except OSError as exc:
        logger.warning("人设卡无法写入，将每次使用默认人设：%s", exc)


def render_personality(name: str, path: str | os.PathLike | None = None) -> str:
    """按名字渲染人设卡。用 replace 而不是 format，避免文件里的花括号报错。"""
    return load_personality(path).replace("{name}", name)


__all__ = [
    "DEFAULT_PERSONALITY",
    "ensure_personality_file",
    "load_personality",
    "render_personality",
]
