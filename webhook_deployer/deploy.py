from __future__ import annotations

import asyncio
import locale
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .config import ProjectConfig
from .logging import TaskLogger
from .sync import SyncResult, sync_tree_preserving


@dataclass(frozen=True)
class DeployRequest:
    project: ProjectConfig
    branch: str
    commit: str


async def run_deployment(request: DeployRequest, logger: TaskLogger) -> int:
    project = request.project
    await logger.write(f"[deploy] start project={project.name} branch={request.branch} commit={request.commit}")
    # 每次部署都强制同步到远端分支状态，避免服务器上遗留的本地修改影响发布结果。
    await _run(["git", "fetch", "origin", request.branch], Path(project.source_dir), logger)
    await _run(["git", "reset", "--hard", f"origin/{request.branch}"], Path(project.source_dir), logger)

    if project.project_type == "vue3":
        await _deploy_vue(project, logger)
    elif project.project_type == "dotnet8-webapi-service":
        await _deploy_dotnet_service(project, logger)
    else:
        raise ValueError(f"unsupported project type: {project.project_type}")

    await logger.write(f"[deploy] succeeded project={project.name}")
    return 0


async def _deploy_vue(project: ProjectConfig, logger: TaskLogger) -> None:
    source_dir = Path(project.source_dir)
    # Vue3 项目统一按 Yarn 项目处理：先锁定依赖安装，再执行可配置的构建命令。
    await _run(["yarn", "install", "--frozen-lockfile"], source_dir, logger)
    await _run(project.build_command, source_dir, logger)
    build_dir = source_dir / project.build_dir
    await logger.write(f"[sync] {build_dir} -> {project.publish_dir}")
    result = sync_tree_preserving(
        build_dir,
        project.publish_dir,
        preserve_files=project.preserve_files,
        preserve_dirs=project.preserve_dirs,
    )
    await logger.write(_sync_summary(result))


async def _deploy_dotnet_service(project: ProjectConfig, logger: TaskLogger) -> None:
    if not project.csproj or not project.service_name:
        raise ValueError(f"dotnet project {project.name} requires csproj and service_name")

    source_dir = Path(project.source_dir)
    service_stopped = False
    temp_dir = Path(tempfile.mkdtemp(prefix=f"{project.name}-publish-"))
    try:
        # 先发布到临时目录，发布成功后再短暂停服同步到服务目录。
        await _run(["dotnet", "restore", project.csproj], source_dir, logger)
        await _run(["dotnet", "publish", project.csproj, "-c", "Release", "-o", str(temp_dir)], source_dir, logger)
        await _run(["powershell", "-NoProfile", "-Command", f"Stop-Service -Name '{project.service_name}'"], source_dir, logger)
        service_stopped = True
        await logger.write(f"[sync] {temp_dir} -> {project.publish_dir}")
        result = sync_tree_preserving(
            temp_dir,
            project.publish_dir,
            preserve_files=project.preserve_files,
            preserve_dirs=project.preserve_dirs,
        )
        await logger.write(_sync_summary(result))
        await _run(["powershell", "-NoProfile", "-Command", f"Start-Service -Name '{project.service_name}'"], source_dir, logger)
        service_stopped = False
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
        if service_stopped:
            try:
                # 任一步骤失败时也尽量拉起服务，避免部署异常后服务长时间停机。
                await _run(
                    ["powershell", "-NoProfile", "-Command", f"Start-Service -Name '{project.service_name}'"],
                    source_dir,
                    logger,
                )
            except Exception as exc:
                await logger.write(f"[service] failed to restart {project.service_name}: {exc}")


async def _run(command: list[str], cwd: Path, logger: TaskLogger) -> None:
    await logger.write(f"[cmd] cwd={cwd} command={_format_command(command)}")
    executable = shutil.which(command[0]) or command[0]
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env.setdefault("DOTNET_CLI_UI_LANGUAGE", "zh-CN")
    # 子进程输出实时写入任务日志，供 /logs/stream 通过 SSE 推送给调用方。
    process = await asyncio.create_subprocess_exec(
        executable,
        *command[1:],
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=env,
    )
    assert process.stdout is not None
    while True:
        line = await process.stdout.readline()
        if not line:
            break
        await logger.write(_decode_process_output(line).rstrip())

    code = await process.wait()
    if code != 0:
        raise RuntimeError(f"command failed with exit code {code}: {_format_command(command)}")


def _format_command(command: list[str]) -> str:
    return " ".join(command)


def _decode_process_output(line: bytes) -> str:
    try:
        return line.decode("utf-8")
    except UnicodeDecodeError:
        return line.decode(locale.getpreferredencoding(False), errors="replace")


def _sync_summary(result: SyncResult) -> str:
    return (
        f"[sync] copied={len(result.copied)} "
        f"deleted={len(result.deleted)} "
        f"preserved={len(result.skipped_preserved)}"
    )
