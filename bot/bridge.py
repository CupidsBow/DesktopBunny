import threading

# 主线程 -> 任意线程 的 Alice 状态快照（纯数据，锁保护）
_snapshot_lock = threading.Lock()
_bunny_snapshot = {}


def set_bunny_snapshot(snapshot: dict):
    """主线程每帧调用，写入 Alice 的纯数据快照。"""
    global _bunny_snapshot
    with _snapshot_lock:
        _bunny_snapshot = dict(snapshot)


def get_bunny_snapshot() -> dict:
    """任意线程调用，读取 Alice 状态快照（返回浅拷贝）。"""
    with _snapshot_lock:
        return dict(_bunny_snapshot)
