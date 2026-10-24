"""身体模块：记录身体数值并派生情绪 / 态度 / 欲望。"""

from manager.body.body import Body
from manager.body.self_state import SelfStateDeriver, derive

__all__ = ["Body", "SelfStateDeriver", "derive"]
