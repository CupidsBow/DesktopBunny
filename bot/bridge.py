import queue
import threading

# bot 线程 -> 主线程 的命令队列（线程安全）
_command_queue = queue.Queue()

# 主线程 -> 任意线程 的兔兔状态快照（纯数据，锁保护）
_snapshot_lock = threading.Lock()
_bunny_snapshot = []


def enqueue_command(cmd: dict):
    """bot 线程调用，把命令塞进队列，由主线程每帧消费。"""
    _command_queue.put(cmd)


def poll_commands() -> list:
    """主线程每帧调用，取出所有待处理命令。"""
    out = []
    while True:
        try:
            out.append(_command_queue.get_nowait())
        except queue.Empty:
            break
    return out


def set_bunnies_snapshot(bunnies: list):
    """主线程每帧调用，写入兔兔状态的纯数据快照。"""
    global _bunny_snapshot
    with _snapshot_lock:
        _bunny_snapshot = bunnies


def get_bunnies_snapshot() -> list:
    """任意线程调用，读取兔兔状态快照（返回浅拷贝）。"""
    with _snapshot_lock:
        return list(_bunny_snapshot)
