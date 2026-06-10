from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path


class TaskLogger:
    def __init__(self, log_file: Path):
        self.log_file = log_file
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        # 每个订阅者持有独立队列，避免慢客户端阻塞日志写入。
        self._subscribers: set[asyncio.Queue[str | None]] = set()
        self._lock = asyncio.Lock()
        self._closed = False

    async def write(self, message: str) -> None:
        # 日志同时落盘和广播，既支持实时查看，也支持任务结束后追溯。
        line = f"{datetime.now().astimezone().isoformat()} {message}"
        async with self._lock:
            with self.log_file.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
            for queue in list(self._subscribers):
                queue.put_nowait(line)

    async def close(self) -> None:
        # None 是日志流结束信号，通知所有 SSE 订阅者退出循环。
        async with self._lock:
            self._closed = True
            for queue in list(self._subscribers):
                queue.put_nowait(None)

    async def stream(self):
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        async with self._lock:
            existing_lines = []
            if self.log_file.exists():
                existing_lines = self.log_file.read_text(encoding="utf-8").splitlines()
            closed = self._closed
            if not closed:
                self._subscribers.add(queue)

        for line in existing_lines:
            yield line
        if closed:
            return

        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                yield item
        finally:
            self._subscribers.discard(queue)
