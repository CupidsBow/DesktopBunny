"""帧生产者：把唤醒理由与只读视图聚合成一帧（情境 + prompt）。"""

from manager.frame_generator.frame_generator import Frame, FrameGenerator
from manager.frame_generator.personality import (
    DEFAULT_PERSONALITY,
    ensure_personality_file,
    render_personality,
)
from manager.frame_generator.prompt import build_messages, system_prompt
from manager.frame_generator.situation import SituationBuilder

__all__ = [
    "DEFAULT_PERSONALITY",
    "Frame",
    "FrameGenerator",
    "SituationBuilder",
    "build_messages",
    "ensure_personality_file",
    "render_personality",
    "system_prompt",
]
