import hashlib
import hmac
import json
import os
import re

from fastapi.testclient import TestClient

from webhook_deployer.app import create_app
from webhook_deployer.config import AppConfig, ProjectConfig, ServerConfig
from webhook_deployer.deploy import DeployRequest


async def test_github_webhook_enqueues_matching_project_push():
    seen = []

    async def runner(request, logger):
        seen.append((request.project.name, request.branch, request.commit))
        return 0

    project = ProjectConfig(
        name="frontend",
        repository="owner/frontend",
        project_type="vue3",
        branches=["main"],
        source_dir="C:/src/frontend",
        webhook_secret="secret",
        publish_dir="C:/www/frontend",
    )
    app = create_app(AppConfig(server=ServerConfig(), projects={"frontend": project}), runner=runner)
    client = TestClient(app)
    body = b'{"ref":"refs/heads/main","after":"abc123","repository":{"full_name":"owner/frontend"}}'
    signature = hmac.new(b"secret", body, hashlib.sha256).hexdigest()

    response = client.post(
        "/webhook/github",
        content=body,
        headers={
            "X-GitHub-Event": "push",
            "X-Hub-Signature-256": f"sha256={signature}",
        },
    )

    assert response.status_code == 202
    assert response.json()["status"] == "queued"
    assert response.json()["task_id"]
    await app.state.deployment_queue.wait_for_idle()
    assert seen == [("frontend", "main", "abc123")]


def test_github_webhook_rejects_invalid_signature():
    project = ProjectConfig(
        name="frontend",
        repository="owner/frontend",
        project_type="vue3",
        branches=["main"],
        source_dir="C:/src/frontend",
        webhook_secret="secret",
        publish_dir="C:/www/frontend",
    )
    app = create_app(AppConfig(server=ServerConfig(), projects={"frontend": project}))
    client = TestClient(app)

    response = client.post(
        "/webhook/github",
        content=b'{"ref":"refs/heads/main","repository":{"full_name":"owner/frontend"}}',
        headers={
            "X-GitHub-Event": "push",
            "X-Hub-Signature-256": "sha256=bad",
        },
    )

    assert response.status_code == 401


async def test_log_stream_without_task_id_uses_latest_memory_task(tmp_path):
    async def runner(request, logger):
        await logger.write(f"commit={request.commit}")
        return 0

    project = ProjectConfig(
        name="frontend",
        repository="owner/frontend",
        project_type="vue3",
        branches=["main"],
        source_dir="C:/src/frontend",
        webhook_secret="secret",
        publish_dir="C:/www/frontend",
    )
    app = create_app(
        AppConfig(server=ServerConfig(log_dir=str(tmp_path)), projects={"frontend": project}),
        runner=runner,
    )
    client = TestClient(app)

    await app.state.deployment_queue.enqueue(DeployRequest(project, "main", "older"))
    await app.state.deployment_queue.enqueue(DeployRequest(project, "main", "newer"))
    await app.state.deployment_queue.wait_for_idle()

    response = client.get("/logs/stream?events=1")

    assert response.status_code == 200
    assert "commit=newer" in response.text
    assert "commit=older" not in response.text


def test_log_stream_with_missing_task_id_does_not_fallback(tmp_path):
    (tmp_path / "frontend").mkdir()
    (tmp_path / "frontend" / "latest.log").write_text("historical\n", encoding="utf-8")
    project = ProjectConfig(
        name="frontend",
        repository="owner/frontend",
        project_type="vue3",
        branches=["main"],
        source_dir="C:/src/frontend",
        webhook_secret="secret",
        publish_dir="C:/www/frontend",
    )
    app = create_app(AppConfig(server=ServerConfig(log_dir=str(tmp_path)), projects={"frontend": project}))
    client = TestClient(app)

    response = client.get("/logs/stream?events=1&task_id=missing")

    assert response.status_code == 404


def test_log_stream_without_task_id_uses_latest_history_when_queue_empty(tmp_path):
    log_dir = tmp_path / "frontend"
    log_dir.mkdir()
    older = log_dir / "older.log"
    newer = log_dir / "newer.log"
    older.write_text("older\n", encoding="utf-8")
    newer.write_text("newer\n", encoding="utf-8")
    os.utime(older, (1, 1))
    os.utime(newer, (2, 2))
    project = ProjectConfig(
        name="frontend",
        repository="owner/frontend",
        project_type="vue3",
        branches=["main"],
        source_dir="C:/src/frontend",
        webhook_secret="secret",
        publish_dir="C:/www/frontend",
    )
    app = create_app(AppConfig(server=ServerConfig(log_dir=str(tmp_path)), projects={"frontend": project}))
    client = TestClient(app)

    response = client.get("/logs/stream?events=1")

    assert response.status_code == 200
    assert '"line": "newer"' in response.text
    assert '"line": "older"' not in response.text


def test_log_stream_without_task_id_returns_404_when_no_logs_exist(tmp_path):
    project = ProjectConfig(
        name="frontend",
        repository="owner/frontend",
        project_type="vue3",
        branches=["main"],
        source_dir="C:/src/frontend",
        webhook_secret="secret",
        publish_dir="C:/www/frontend",
    )
    app = create_app(AppConfig(server=ServerConfig(log_dir=str(tmp_path)), projects={"frontend": project}))
    client = TestClient(app)

    response = client.get("/logs/stream?events=1")

    assert response.status_code == 404


def test_tasks_endpoint_is_removed():
    project = ProjectConfig(
        name="frontend",
        repository="owner/frontend",
        project_type="vue3",
        branches=["main"],
        source_dir="C:/src/frontend",
        webhook_secret="secret",
        publish_dir="C:/www/frontend",
    )
    app = create_app(AppConfig(server=ServerConfig(), projects={"frontend": project}))
    client = TestClient(app)

    response = client.get("/tasks/anything")

    assert response.status_code == 404


def test_log_stream_returns_html_page_by_default(tmp_path):
    project = ProjectConfig(
        name="frontend",
        repository="owner/frontend",
        project_type="vue3",
        branches=["main"],
        source_dir="C:/src/frontend",
        webhook_secret="secret",
        publish_dir="C:/www/frontend",
    )
    app = create_app(AppConfig(server=ServerConfig(log_dir=str(tmp_path)), projects={"frontend": project}))
    client = TestClient(app)

    response = client.get("/logs/stream")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "charset=utf-8" in response.headers["content-type"].lower()
    assert 'id="task-links"' in response.text
    assert 'id="log-output"' in response.text
    assert "/logs/stream?events=1" in response.text


def test_log_stream_page_closes_event_source_on_end(tmp_path):
    project = ProjectConfig(
        name="frontend",
        repository="owner/frontend",
        project_type="vue3",
        branches=["main"],
        source_dir="C:/src/frontend",
        webhook_secret="secret",
        publish_dir="C:/www/frontend",
    )
    app = create_app(AppConfig(server=ServerConfig(log_dir=str(tmp_path)), projects={"frontend": project}))
    client = TestClient(app)

    response = client.get("/logs/stream")

    assert response.status_code == 200
    assert re.search(
        r'source\.addEventListener\("end",\s*\(\)\s*=>\s*\{[^}]*source\.close\(\)',
        response.text,
        re.DOTALL,
    )


def test_log_stream_history_limits_finished_tasks_to_two_per_project(tmp_path):
    frontend_log_dir = tmp_path / "frontend"
    frontend_log_dir.mkdir()
    for index in range(25):
        path = frontend_log_dir / f"frontend-{index}.log"
        path.write_text(f"frontend {index}\n", encoding="utf-8")
        os.utime(path, (100 + index, 100 + index))
    api_log_dir = tmp_path / "api"
    api_log_dir.mkdir()
    for index in range(4):
        path = api_log_dir / f"api-{index}.log"
        path.write_text(f"api {index}\n", encoding="utf-8")
        os.utime(path, (index + 1, index + 1))
    projects = {
        name: ProjectConfig(
            name=name,
            repository=f"owner/{name}",
            project_type="vue3",
            branches=["main"],
            source_dir=f"C:/src/{name}",
            webhook_secret="secret",
            publish_dir=f"C:/www/{name}",
        )
        for name in ("frontend", "api")
    }
    app = create_app(AppConfig(server=ServerConfig(log_dir=str(tmp_path)), projects=projects))
    client = TestClient(app)

    response = client.get("/logs/stream?events=1")
    snapshot = json.loads(re.search(r"event: snapshot\ndata: (.+)", response.text).group(1))
    projects_seen = [task["project"] for task in snapshot["finished_recent"]]

    assert response.status_code == 200
    assert projects_seen.count("frontend") == 2
    assert projects_seen.count("api") == 2
    assert len(snapshot["finished_recent"]) == 4


async def test_log_stream_events_include_snapshot_for_concurrent_tasks(tmp_path):
    release_a = False
    release_b = False

    async def runner(request, logger):
        await logger.write(f"commit={request.commit}")
        while (request.project.name == "a" and not release_a) or (request.project.name == "b" and not release_b):
            await logger.write(f"waiting={request.project.name}")
            break
        return 0

    project_a = ProjectConfig(
        name="a",
        repository="owner/a",
        project_type="vue3",
        branches=["main"],
        source_dir="C:/src/a",
        webhook_secret="secret",
        publish_dir="C:/www/a",
    )
    project_b = ProjectConfig(
        name="b",
        repository="owner/b",
        project_type="vue3",
        branches=["main"],
        source_dir="C:/src/b",
        webhook_secret="secret",
        publish_dir="C:/www/b",
    )
    app = create_app(
        AppConfig(server=ServerConfig(log_dir=str(tmp_path)), projects={"a": project_a, "b": project_b}),
        runner=runner,
    )
    client = TestClient(app)

    await app.state.deployment_queue.enqueue(DeployRequest(project_a, "main", "1"))
    await app.state.deployment_queue.enqueue(DeployRequest(project_b, "main", "2"))
    await app.state.deployment_queue.wait_for_idle()

    response = client.get("/logs/stream?events=1")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "charset=utf-8" in response.headers["content-type"].lower()
    assert "event: snapshot" in response.text
    assert '"finished_recent"' in response.text


async def test_log_stream_events_read_history_by_task_id(tmp_path):
    log_dir = tmp_path / "frontend"
    log_dir.mkdir()
    log_file = log_dir / "abc123.log"
    log_file.write_text("部署完成\n", encoding="utf-8")
    project = ProjectConfig(
        name="frontend",
        repository="owner/frontend",
        project_type="vue3",
        branches=["main"],
        source_dir="C:/src/frontend",
        webhook_secret="secret",
        publish_dir="C:/www/frontend",
    )
    app = create_app(AppConfig(server=ServerConfig(log_dir=str(tmp_path)), projects={"frontend": project}))
    client = TestClient(app)

    response = client.get("/logs/stream?events=1&task_id=abc123")

    assert response.status_code == 200
    assert "event: log" in response.text
    assert "部署完成" in response.text


async def test_log_stream_events_missing_task_id_returns_404(tmp_path):
    project = ProjectConfig(
        name="frontend",
        repository="owner/frontend",
        project_type="vue3",
        branches=["main"],
        source_dir="C:/src/frontend",
        webhook_secret="secret",
        publish_dir="C:/www/frontend",
    )
    app = create_app(AppConfig(server=ServerConfig(log_dir=str(tmp_path)), projects={"frontend": project}))
    client = TestClient(app)

    response = client.get("/logs/stream?events=1&task_id=missing")

    assert response.status_code == 404


async def test_log_stream_events_preserve_utf8_chinese(tmp_path):
    async def runner(request, logger):
        await logger.write("部署完成")
        return 0

    project = ProjectConfig(
        name="frontend",
        repository="owner/frontend",
        project_type="vue3",
        branches=["main"],
        source_dir="C:/src/frontend",
        webhook_secret="secret",
        publish_dir="C:/www/frontend",
    )
    app = create_app(
        AppConfig(server=ServerConfig(log_dir=str(tmp_path)), projects={"frontend": project}),
        runner=runner,
    )
    client = TestClient(app)

    task = await app.state.deployment_queue.enqueue(DeployRequest(project, "main", "utf8"))
    await app.state.deployment_queue.wait_for_idle()

    response = client.get(f"/logs/stream?events=1&task_id={task.id}")

    assert "部署完成" in response.text
    assert "�" not in response.text
    assert re.search(r"\d{4}-\d{2}-\d{2}T", response.text)
