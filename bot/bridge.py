import queue

# bot 线程 -> 主线程 的命令队列（线程安全）
_command_queue = queue.Queue()


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
