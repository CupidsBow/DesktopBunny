"""事件队列：存储任何可以触发 Alice 思考并决策的事件，负责持久化与崩溃恢复。"""

from manager.event_queue.event_queue import EventQueue

__all__ = ["EventQueue"]
