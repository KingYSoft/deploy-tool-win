import asyncio

from webhook_deployer.logging import TaskLogger


async def test_stream_replays_existing_log_lines_after_close(tmp_path):
    logger = TaskLogger(tmp_path / "task.log")

    await logger.write("first")
    await logger.close()

    async def collect_stream():
        return [line async for line in logger.stream()]

    lines = await asyncio.wait_for(collect_stream(), timeout=0.1)

    assert len(lines) == 1
    assert lines[0].endswith(" first")
