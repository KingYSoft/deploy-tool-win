import asyncio

from webhook_deployer.config import ProjectConfig
from webhook_deployer.deploy import DeployRequest
from webhook_deployer.queue import DeploymentQueue


def project(name: str) -> ProjectConfig:
    return ProjectConfig(
        name=name,
        repository=f"owner/{name}",
        project_type="vue3",
        branches=["main"],
        source_dir="C:/src",
        webhook_secret="secret",
        publish_dir="C:/www",
    )


async def test_queue_runs_same_project_serially_and_different_projects_concurrently():
    events: list[str] = []
    release_a = asyncio.Event()
    release_b = asyncio.Event()

    async def runner(request, logger):
        events.append(f"start:{request.project.name}:{request.commit}")
        if request.project.name == "a":
            await release_a.wait()
        else:
            await release_b.wait()
        events.append(f"end:{request.project.name}:{request.commit}")
        return 0

    queue = DeploymentQueue(runner=runner)
    first = await queue.enqueue(DeployRequest(project("a"), "main", "1"))
    second = await queue.enqueue(DeployRequest(project("a"), "main", "2"))
    third = await queue.enqueue(DeployRequest(project("b"), "main", "3"))

    await asyncio.sleep(0.05)

    assert "start:a:1" in events
    assert "start:b:3" in events
    assert "start:a:2" not in events

    release_a.set()
    release_b.set()
    await queue.wait_for_idle()

    assert first.status == "succeeded"
    assert second.status == "succeeded"
    assert third.status == "succeeded"
    assert events.index("end:a:1") < events.index("start:a:2")


async def test_latest_task_prefers_latest_running_task():
    release_first = asyncio.Event()
    release_second = asyncio.Event()

    async def runner(request, logger):
        if request.commit == "1":
            await release_first.wait()
        elif request.commit == "2":
            await release_second.wait()
        return 0

    queue = DeploymentQueue(runner=runner)
    first = await queue.enqueue(DeployRequest(project("a"), "main", "1"))
    second = await queue.enqueue(DeployRequest(project("b"), "main", "2"))

    await asyncio.sleep(0.05)

    assert queue.latest_task() is second

    release_second.set()
    await asyncio.sleep(0.05)

    assert queue.latest_task() is first

    release_first.set()
    await queue.wait_for_idle()


async def test_latest_task_falls_back_to_latest_created_task():
    async def runner(request, logger):
        return 0

    queue = DeploymentQueue(runner=runner)
    first = await queue.enqueue(DeployRequest(project("a"), "main", "1"))
    second = await queue.enqueue(DeployRequest(project("b"), "main", "2"))

    await queue.wait_for_idle()

    assert first.status == "succeeded"
    assert queue.latest_task() is second


async def test_snapshot_lists_running_and_queued_tasks():
    release_first = asyncio.Event()
    release_third = asyncio.Event()

    async def runner(request, logger):
        if request.project.name == "a" and request.commit == "1":
            await release_first.wait()
        elif request.project.name == "b":
            await release_third.wait()
        return 0

    queue = DeploymentQueue(runner=runner)
    first = await queue.enqueue(DeployRequest(project("a"), "main", "1"))
    second = await queue.enqueue(DeployRequest(project("a"), "main", "2"))
    third = await queue.enqueue(DeployRequest(project("b"), "main", "3"))

    await asyncio.sleep(0.05)

    snapshot = queue.snapshot()

    assert {task["id"] for task in snapshot["running"]} == {first.id, third.id}
    assert [task["id"] for task in snapshot["queued"]] == [second.id]

    release_first.set()
    release_third.set()
    await queue.wait_for_idle()
