from pathlib import Path

from webhook_deployer.config import load_config


def test_load_config_parses_multiple_projects_and_requires_secrets(tmp_path: Path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
server:
  host: 127.0.0.1
  port: 9000
projects:
  - name: frontend
    repository: owner/frontend
    type: vue3
    branches: [main]
    source_dir: C:/sites/frontend-src
    webhook_secret: secret-1
    publish_dir: C:/sites/frontend
    preserve_files: [config.js]
    preserve_dirs: [uploads]
  - name: api
    repository: owner/api
    type: dotnet8-webapi-service
    branches: [production]
    source_dir: C:/sites/api-src
    webhook_secret: secret-2
    csproj: src/Api/Api.csproj
    service_name: ApiService
    publish_dir: C:/sites/api
    preserve_files: [appsettings.Production.json]
    preserve_dirs: [logs]
""",
        encoding="utf-8",
    )

    config = load_config(config_file)

    assert config.server.host == "127.0.0.1"
    assert config.server.port == 9000
    assert config.projects["frontend"].project_type == "vue3"
    assert config.projects["frontend"].build_command == ["yarn", "build:test"]
    assert config.projects["api"].service_name == "ApiService"


def test_load_config_rejects_project_without_webhook_secret(tmp_path: Path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
projects:
  - name: frontend
    repository: owner/frontend
    type: vue3
    branches: [main]
    source_dir: C:/sites/frontend-src
    publish_dir: C:/sites/frontend
""",
        encoding="utf-8",
    )

    try:
        load_config(config_file)
    except ValueError as exc:
        assert "webhook_secret" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_load_config_treats_empty_optional_lists_as_empty_lists(tmp_path: Path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
projects:
  - name: frontend
    repository: owner/frontend
    type: vue3
    branches: [main]
    source_dir: C:/sites/frontend-src
    webhook_secret: secret-1
    publish_dir: C:/sites/frontend
    preserve_files:
    preserve_dirs:
    build_command:
""",
        encoding="utf-8",
    )

    config = load_config(config_file)

    project = config.projects["frontend"]
    assert project.preserve_files == []
    assert project.preserve_dirs == []
    assert project.build_command == ["yarn", "build:test"]
