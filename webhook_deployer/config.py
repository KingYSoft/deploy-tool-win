from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

ProjectType = Literal["vue3", "dotnet8-webapi-service"]


@dataclass(frozen=True)
class ServerConfig:
    # HTTP 服务监听配置；命令行参数可以在启动时覆盖 host/port。
    host: str = "0.0.0.0"
    port: int = 9000
    log_dir: str = "logs"


@dataclass(frozen=True)
class ProjectConfig:
    # 每个项目用 GitHub 仓库全名和分支匹配 webhook 事件。
    name: str
    repository: str
    project_type: ProjectType
    branches: list[str]
    source_dir: str
    webhook_secret: str
    publish_dir: str
    # 发布目录中的这些路径会被保护，不会在同步时覆盖或删除。
    preserve_files: list[str] = field(default_factory=list)
    preserve_dirs: list[str] = field(default_factory=list)
    build_dir: str = "dist"
    build_command: list[str] = field(default_factory=lambda: ["yarn", "build:test"])
    csproj: str | None = None
    service_name: str | None = None


@dataclass(frozen=True)
class AppConfig:
    server: ServerConfig
    projects: dict[str, ProjectConfig]


def load_config(path: str | Path) -> AppConfig:
    # 配置文件允许只写业务项目；server 字段缺失时使用安全默认值。
    with Path(path).open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}

    server_raw = raw.get("server") or {}
    server = ServerConfig(
        host=str(server_raw.get("host", "0.0.0.0")),
        port=int(server_raw.get("port", 9000)),
        log_dir=str(server_raw.get("log_dir", "logs")),
    )

    projects: dict[str, ProjectConfig] = {}
    for item in raw.get("projects") or []:
        project = _parse_project(item)
        if project.name in projects:
            raise ValueError(f"duplicate project name: {project.name}")
        projects[project.name] = project

    if not projects:
        raise ValueError("config must define at least one project")

    return AppConfig(server=server, projects=projects)


def _parse_project(raw: dict[str, Any]) -> ProjectConfig:
    # 基础字段缺失会导致 webhook 无法匹配或部署无目标，因此在启动阶段直接失败。
    required = ["name", "repository", "type", "branches", "source_dir", "webhook_secret", "publish_dir"]
    for key in required:
        if not raw.get(key):
            raise ValueError(f"project {raw.get('name', '<unknown>')} missing required field: {key}")

    project_type = raw["type"]
    if project_type not in ("vue3", "dotnet8-webapi-service"):
        raise ValueError(f"unsupported project type: {project_type}")

    if project_type == "dotnet8-webapi-service":
        # .NET Windows 服务部署必须知道项目文件和服务名，才能发布并重启服务。
        for key in ("csproj", "service_name"):
            if not raw.get(key):
                raise ValueError(f"project {raw['name']} missing required field: {key}")

    return ProjectConfig(
        name=str(raw["name"]),
        repository=str(raw["repository"]),
        project_type=project_type,
        branches=[str(branch) for branch in raw["branches"]],
        source_dir=str(raw["source_dir"]),
        webhook_secret=str(raw["webhook_secret"]),
        publish_dir=str(raw["publish_dir"]),
        preserve_files=_optional_list(raw, "preserve_files", []),
        preserve_dirs=_optional_list(raw, "preserve_dirs", []),
        build_dir=str(raw.get("build_dir", "dist")),
        build_command=_optional_list(raw, "build_command", ["yarn", "build:test"]),
        csproj=str(raw["csproj"]) if raw.get("csproj") else None,
        service_name=str(raw["service_name"]) if raw.get("service_name") else None,
    )


def _optional_list(raw: dict[str, Any], key: str, default: list[str]) -> list[str]:
    # YAML 中只写 `key:` 会解析成 None；可选列表字段统一按默认值处理。
    value = raw.get(key)
    if value is None:
        return list(default)
    return [str(part) for part in value]
