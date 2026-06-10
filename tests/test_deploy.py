from pathlib import Path
import sys

import pytest

from webhook_deployer.config import ProjectConfig
from webhook_deployer.deploy import DeployRequest, _run, run_deployment
from webhook_deployer.sync import SyncResult


class MemoryLogger:
    def __init__(self):
        self.lines: list[str] = []

    async def write(self, message: str) -> None:
        self.lines.append(message)


@pytest.mark.asyncio
async def test_vue_deployment_uses_yarn_install_and_build_test_by_default(tmp_path: Path, monkeypatch):
    source_dir = tmp_path / "source"
    publish_dir = tmp_path / "publish"
    dist_dir = source_dir / "dist"
    dist_dir.mkdir(parents=True)
    (dist_dir / "index.html").write_text("built", encoding="utf-8")

    commands: list[list[str]] = []

    async def fake_run(command, cwd, logger):
        commands.append(command)

    monkeypatch.setattr("webhook_deployer.deploy._run", fake_run)

    project = ProjectConfig(
        name="frontend",
        repository="owner/frontend",
        project_type="vue3",
        branches=["main"],
        source_dir=str(source_dir),
        webhook_secret="secret",
        publish_dir=str(publish_dir),
    )

    await run_deployment(DeployRequest(project=project, branch="main", commit="abc123"), MemoryLogger())

    assert commands == [
        ["git", "fetch", "origin", "main"],
        ["git", "reset", "--hard", "origin/main"],
        ["yarn", "install", "--frozen-lockfile"],
        ["yarn", "build:test"],
    ]
    assert (publish_dir / "index.html").read_text(encoding="utf-8") == "built"


@pytest.mark.asyncio
async def test_deployment_sync_summary_logs_preserved_count_without_file_names(tmp_path: Path, monkeypatch):
    source_dir = tmp_path / "source"
    publish_dir = tmp_path / "publish"
    dist_dir = source_dir / "dist"
    dist_dir.mkdir(parents=True)
    publish_dir.mkdir()
    (dist_dir / "index.html").write_text("built", encoding="utf-8")
    (dist_dir / "config.js").write_text("new config", encoding="utf-8")
    (publish_dir / "config.js").write_text("old config", encoding="utf-8")

    async def fake_run(command, cwd, logger):
        return None

    monkeypatch.setattr("webhook_deployer.deploy._run", fake_run)
    logger = MemoryLogger()
    project = ProjectConfig(
        name="frontend",
        repository="owner/frontend",
        project_type="vue3",
        branches=["main"],
        source_dir=str(source_dir),
        webhook_secret="secret",
        publish_dir=str(publish_dir),
        preserve_files=["config.js"],
    )

    await run_deployment(DeployRequest(project=project, branch="main", commit="abc123"), logger)

    sync_summary = next(line for line in logger.lines if line.startswith("[sync] copied="))
    assert "preserved=1" in sync_summary
    assert "config.js" not in sync_summary


@pytest.mark.asyncio
async def test_dotnet_service_publishes_before_stopping_service(tmp_path: Path, monkeypatch):
    source_dir = tmp_path / "source"
    publish_dir = tmp_path / "publish"
    source_dir.mkdir()

    events: list[str] = []

    async def fake_run(command, cwd, logger):
        if command[0] == "dotnet":
            events.append(" ".join(command[:2]))
        elif "Stop-Service" in command[-1]:
            events.append("stop-service")
        elif "Start-Service" in command[-1]:
            events.append("start-service")
        else:
            events.append(" ".join(command[:2]))

    def fake_sync_tree_preserving(source, target, *, preserve_files=None, preserve_dirs=None):
        events.append("sync")
        return SyncResult()

    monkeypatch.setattr("webhook_deployer.deploy._run", fake_run)
    monkeypatch.setattr("webhook_deployer.deploy.sync_tree_preserving", fake_sync_tree_preserving)

    project = ProjectConfig(
        name="api",
        repository="owner/api",
        project_type="dotnet8-webapi-service",
        branches=["main"],
        source_dir=str(source_dir),
        webhook_secret="secret",
        publish_dir=str(publish_dir),
        csproj="Api.csproj",
        service_name="ApiService",
    )

    await run_deployment(DeployRequest(project=project, branch="main", commit="abc123"), MemoryLogger())

    assert events == [
        "git fetch",
        "git reset",
        "dotnet restore",
        "dotnet publish",
        "stop-service",
        "sync",
        "start-service",
    ]


@pytest.mark.asyncio
async def test_dotnet_service_publish_failure_does_not_stop_service(tmp_path: Path, monkeypatch):
    source_dir = tmp_path / "source"
    publish_dir = tmp_path / "publish"
    source_dir.mkdir()

    commands: list[list[str]] = []

    async def fake_run(command, cwd, logger):
        commands.append(command)
        if command[:2] == ["dotnet", "publish"]:
            raise RuntimeError("publish failed")

    monkeypatch.setattr("webhook_deployer.deploy._run", fake_run)

    project = ProjectConfig(
        name="api",
        repository="owner/api",
        project_type="dotnet8-webapi-service",
        branches=["main"],
        source_dir=str(source_dir),
        webhook_secret="secret",
        publish_dir=str(publish_dir),
        csproj="Api.csproj",
        service_name="ApiService",
    )

    with pytest.raises(RuntimeError, match="publish failed"):
        await run_deployment(DeployRequest(project=project, branch="main", commit="abc123"), MemoryLogger())

    service_commands = [command for command in commands if command[0] == "powershell"]
    assert service_commands == []


@pytest.mark.asyncio
async def test_run_resolves_windows_command_shims_before_starting_process(tmp_path: Path, monkeypatch):
    started: dict[str, object] = {}

    class EmptyStdout:
        async def readline(self):
            return b""

    class FakeProcess:
        stdout = EmptyStdout()

        async def wait(self):
            return 0

    async def fake_create_subprocess_exec(*args, **kwargs):
        started["args"] = args
        started["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr("webhook_deployer.deploy.shutil.which", lambda command: r"C:\tools\yarn.CMD")
    monkeypatch.setattr("webhook_deployer.deploy.asyncio.create_subprocess_exec", fake_create_subprocess_exec)

    await _run(["yarn", "--version"], tmp_path, MemoryLogger())

    assert started["args"] == (r"C:\tools\yarn.CMD", "--version")


@pytest.mark.asyncio
async def test_run_decodes_utf8_subprocess_output(tmp_path: Path):
    logger = MemoryLogger()

    await _run([sys.executable, "-c", "print('部署完成')"], tmp_path, logger)

    assert "部署完成" in logger.lines
    assert all("�" not in line for line in logger.lines)


@pytest.mark.asyncio
async def test_run_sets_utf8_environment_for_subprocesses(tmp_path: Path, monkeypatch):
    started: dict[str, object] = {}

    class EmptyStdout:
        async def readline(self):
            return b""

    class FakeProcess:
        stdout = EmptyStdout()

        async def wait(self):
            return 0

    async def fake_create_subprocess_exec(*args, **kwargs):
        started["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr("webhook_deployer.deploy.asyncio.create_subprocess_exec", fake_create_subprocess_exec)

    await _run(["tool"], tmp_path, MemoryLogger())

    env = started["kwargs"]["env"]
    assert env["PYTHONUTF8"] == "1"
    assert env["PYTHONIOENCODING"] == "utf-8"
