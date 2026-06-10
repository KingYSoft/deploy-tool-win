from __future__ import annotations

import asyncio
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Awaitable, Callable

from .deploy import DeployRequest, run_deployment
from .logging import TaskLogger

TaskStatus = str
Runner = Callable[[DeployRequest, TaskLogger], Awaitable[int]]


@dataclass
class DeploymentTask:
    id: str
    request: DeployRequest
    status: TaskStatus = "queued"
    created_at: datetime = field(default_factory=lambda: datetime.now().astimezone())
    started_at: datetime | None = None
    finished_at: datetime | None = None
    exit_code: int | None = None
    error: str | None = None
    logger: TaskLogger | None = None


class DeploymentQueue:
    def __init__(self, *, runner: Runner = run_deployment, log_dir: str | Path = "logs"):
        self._runner = runner
        self._log_dir = Path(log_dir)
        # 同一项目串行部署，不同项目可以并发部署，避免同一发布目录互相覆盖。
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._tasks: dict[str, DeploymentTask] = {}
        self._background: set[asyncio.Task[None]] = set()

    async def enqueue(self, request: DeployRequest) -> DeploymentTask:
        # 入队后立即创建后台任务，HTTP webhook 可以快速返回 task_id。
        task = DeploymentTask(id=uuid.uuid4().hex, request=request)
        task.logger = TaskLogger(self._log_dir / request.project.name / f"{task.id}.log")
        self._tasks[task.id] = task
        background = asyncio.create_task(self._run_task(task))
        self._background.add(background)
        background.add_done_callback(self._background.discard)
        return task

    def get(self, task_id: str) -> DeploymentTask | None:
        return self._tasks.get(task_id)

    def latest_task(self) -> DeploymentTask | None:
        running = [task for task in self._tasks.values() if task.status == "running" and task.started_at is not None]
        if running:
            return max(running, key=lambda task: task.started_at or task.created_at)
        if not self._tasks:
            return None
        return max(self._tasks.values(), key=lambda task: task.created_at)

    def snapshot(self) -> dict[str, list[dict[str, object]]]:
        tasks = sorted(self._tasks.values(), key=lambda task: task.created_at)
        return {
            "queued": [self._task_summary(task) for task in tasks if task.status == "queued"],
            "running": [self._task_summary(task) for task in tasks if task.status == "running"],
            "finished_recent": [
                self._task_summary(task)
                for task in sorted(
                    [task for task in tasks if task.status in {"succeeded", "failed"}],
                    key=lambda task: task.finished_at or task.created_at,
                    reverse=True,
                )[:20]
            ],
        }

    async def wait_for_idle(self) -> None:
        # 测试辅助方法：等待当前所有后台部署任务结束。
        while self._background:
            await asyncio.gather(*list(self._background), return_exceptions=True)

    async def _run_task(self, task: DeploymentTask) -> None:
        assert task.logger is not None
        lock = self._locks[task.request.project.name]
        async with lock:
            # 状态字段用于队列快照查询，异常会记录到任务日志。
            task.status = "running"
            task.started_at = datetime.now().astimezone()
            try:
                task.exit_code = await self._runner(task.request, task.logger)
                task.status = "succeeded"
            except Exception as exc:
                task.exit_code = 1
                task.error = str(exc)
                task.status = "failed"
                await task.logger.write(f"[error] {exc}")
            finally:
                task.finished_at = datetime.now().astimezone()
                await task.logger.close()

    @staticmethod
    def _task_summary(task: DeploymentTask) -> dict[str, object]:
        return {
            "id": task.id,
            "project": task.request.project.name,
            "branch": task.request.branch,
            "commit": task.request.commit,
            "status": task.status,
            "created_at": task.created_at.isoformat(),
            "started_at": task.started_at.isoformat() if task.started_at else None,
            "finished_at": task.finished_at.isoformat() if task.finished_at else None,
            "exit_code": task.exit_code,
            "error": task.error,
        }
