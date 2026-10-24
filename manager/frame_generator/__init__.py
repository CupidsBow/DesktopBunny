"""帧生产者：把唤醒理由与只读视图聚合成一帧（情境 + prompt）。"""

from manager.frame_generator.frame_generator import Frame, FrameGenerator
from manager.frame_generator.prompt import L0_CORE, build_messages
from manager.frame_generator.situation import SituationBuilder

__all__ = [
    "Frame",
    "FrameGenerator",
    "L0_CORE",
    "SituationBuilder",
    "build_messages",
]
