from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, StreamingResponse

from .config import AppConfig
from .deploy import DeployRequest
from .queue import DeploymentQueue, DeploymentTask, Runner
from .security import verify_github_signature


def create_app(config: AppConfig, *, runner: Runner | None = None) -> FastAPI:
    app = FastAPI(title="GitHub Webhook Deployer")
    # 测试可以注入 runner；生产环境默认使用真实部署函数。
    if runner is None:
        queue = DeploymentQueue(log_dir=config.server.log_dir)
    else:
        queue = DeploymentQueue(runner=runner, log_dir=config.server.log_dir)
    app.state.deployment_queue = queue
    app.state.config = config

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.post("/webhook/github", status_code=202)
    async def github_webhook(
        request: Request,
        x_github_event: str | None = Header(default=None),
        x_hub_signature_256: str | None = Header(default=None),
    ):
        # 只处理 push 事件，其他 GitHub 事件直接忽略，避免误触发部署。
        if x_github_event != "push":
            return {"status": "ignored", "reason": "unsupported event"}

        body = await request.body()
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail="invalid JSON payload") from exc

        repository = (payload.get("repository") or {}).get("full_name")
        branch = _branch_from_ref(str(payload.get("ref", "")))
        project = _match_project(config, repository, branch)
        if project is None:
            return {"status": "ignored", "reason": "repository or branch not configured"}

        # 签名校验必须在入队前完成；每个项目可以配置独立 webhook secret。
        if not verify_github_signature(body, x_hub_signature_256, project.webhook_secret):
            raise HTTPException(status_code=401, detail="invalid signature")

        task = await queue.enqueue(DeployRequest(project=project, branch=branch, commit=str(payload.get("after", ""))))
        return {"status": "queued", "task_id": task.id}

    @app.get("/logs/stream")
    async def stream_logs(task_id: str | None = None, events: str | None = None):
        if events == "1":
            return _stream_log_events(queue, Path(config.server.log_dir), task_id)
        return HTMLResponse(_logs_page(_snapshot_with_history(queue, Path(config.server.log_dir))), media_type="text/html; charset=utf-8")

    return app


def _match_project(config: AppConfig, repository: str | None, branch: str):
    # 同一个服务可以托管多个仓库；仓库全名和分支都匹配才允许部署。
    for project in config.projects.values():
        if project.repository == repository and branch in project.branches:
            return project
    return None


def _branch_from_ref(ref: str) -> str:
    # GitHub push payload 使用 refs/heads/main 形式，这里转成配置中的分支名。
    prefix = "refs/heads/"
    if ref.startswith(prefix):
        return ref[len(prefix) :]
    return ref


def _stream_log_events(queue: DeploymentQueue, log_dir: Path, task_id: str | None):
    if task_id:
        task = queue.get(task_id)
        if task is not None and task.status == "running" and task.logger is not None:
            return StreamingResponse(
                _task_log_stream(task, include_snapshot=False),
                media_type="text/event-stream; charset=utf-8",
            )
        if task is not None:
            log_file = task.logger.log_file if task.logger is not None else _find_log_file(log_dir, task_id)
            return _plain_log_response(log_file)

        log_file = _find_log_file(log_dir, task_id)
        if log_file is None:
            raise HTTPException(status_code=404, detail="task not found")
        return _plain_log_response(log_file)

    snapshot = _snapshot_with_history(queue, log_dir)
    task = queue.latest_task()
    if task is not None and task.status == "running" and task.logger is not None:
        return StreamingResponse(
            _task_log_stream(task, snapshot=snapshot),
            media_type="text/event-stream; charset=utf-8",
        )
    if task is not None and task.logger is not None:
        return _plain_log_response(task.logger.log_file)

    log_file = _latest_log_file(log_dir)
    if log_file is None:
        raise HTTPException(status_code=404, detail="task not found")
    return _plain_log_response(log_file)


def _plain_log_response(log_file: Path | None):
    if log_file is None:
        raise HTTPException(status_code=404, detail="task not found")
    if not log_file.exists():
        return PlainTextResponse("", media_type="text/plain; charset=utf-8")
    return PlainTextResponse(log_file.read_text(encoding="utf-8"), media_type="text/plain; charset=utf-8")


async def _task_log_stream(
    task: DeploymentTask,
    *,
    snapshot: dict[str, list[dict[str, object]]] | None = None,
    include_snapshot: bool = True,
):
    assert task.logger is not None
    # 通过 SSE 推送部署日志，新日志写入后会立即送达客户端。
    if include_snapshot:
        yield _sse_event("snapshot", snapshot or {"queued": [], "running": [], "finished_recent": []})
    async for line in task.logger.stream():
        yield _sse_event(
            "log",
            {
                "task_id": task.id,
                "project": task.request.project.name,
                "line": line,
            },
        )
    yield _sse_event("end", {"task_id": task.id})


async def _file_log_stream(
    log_file: Path,
    *,
    task_id: str,
    project: str,
    snapshot: dict[str, list[dict[str, object]]] | None = None,
    include_snapshot: bool = True,
):
    if include_snapshot:
        yield _sse_event("snapshot", snapshot or {"queued": [], "running": [], "finished_recent": []})
    with log_file.open("r", encoding="utf-8") as fh:
        for line in fh:
            yield _sse_event("log", {"task_id": task_id, "project": project, "line": line.rstrip()})
    yield _sse_event("end", {"task_id": task_id})


def _sse_event(event: str, data: dict[str, object]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _snapshot_with_history(queue: DeploymentQueue, log_dir: Path) -> dict[str, list[dict[str, object]]]:
    snapshot = queue.snapshot()
    seen = {str(task["id"]) for task in snapshot["finished_recent"]}
    for item in _history_log_summaries(log_dir):
        if str(item["id"]) not in seen:
            snapshot["finished_recent"].append(item)
            seen.add(str(item["id"]))
    snapshot["finished_recent"] = _limit_history_per_project(snapshot["finished_recent"])
    return snapshot


def _history_log_summaries(log_dir: Path) -> list[dict[str, object]]:
    if not log_dir.exists():
        return []
    logs = sorted([path for path in log_dir.rglob("*.log") if path.is_file()], key=lambda path: path.stat().st_mtime, reverse=True)
    summaries = [
        {
            "id": path.stem,
            "project": path.parent.name,
            "branch": None,
            "commit": None,
            "status": "historical",
            "created_at": None,
            "started_at": None,
            "finished_at": _mtime_iso(path),
            "exit_code": None,
            "error": None,
        }
        for path in logs
    ]
    return _limit_history_per_project(summaries)


def _limit_history_per_project(items: list[dict[str, object]], limit: int = 2) -> list[dict[str, object]]:
    counts: dict[str, int] = {}
    limited = []
    for item in items:
        project = str(item.get("project") or "")
        count = counts.get(project, 0)
        if count >= limit:
            continue
        limited.append(item)
        counts[project] = count + 1
    return limited


def _mtime_iso(path: Path) -> str:
    from datetime import datetime

    return datetime.fromtimestamp(max(path.stat().st_mtime, 86400)).astimezone().isoformat()


def _find_log_file(log_dir: Path, task_id: str) -> Path | None:
    if not log_dir.exists():
        return None
    for path in log_dir.rglob(f"{task_id}.log"):
        if path.is_file():
            return path
    return None


def _latest_log_file(log_dir: Path) -> Path | None:
    if not log_dir.exists():
        return None
    log_files = [path for path in log_dir.rglob("*.log") if path.is_file()]
    if not log_files:
        return None
    return max(log_files, key=lambda path: path.stat().st_mtime)


def _logs_page(snapshot: dict[str, list[dict[str, object]]]) -> str:
    initial_snapshot = json.dumps(snapshot, ensure_ascii=False).replace("</", "<\\/")
    html = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Deployment Logs</title>
  <style>
    :root { color-scheme: dark; --bg: #101214; --panel: #191d20; --line: #2b3136; --text: #e7ecef; --muted: #93a0a8; --accent: #2dd4bf; --warn: #f8c14a; }
    * { box-sizing: border-box; }
    body { margin: 0; min-height: 100vh; background: var(--bg); color: var(--text); font-family: Consolas, "Cascadia Mono", "Microsoft YaHei UI", monospace; }
    header { padding: 18px 22px; border-bottom: 1px solid var(--line); display: flex; justify-content: space-between; gap: 16px; align-items: center; }
    h1 { margin: 0; font-size: 18px; font-weight: 700; letter-spacing: 0; }
    main { display: grid; grid-template-columns: minmax(260px, 360px) 1fr; min-height: calc(100vh - 61px); }
    aside { border-right: 1px solid var(--line); background: var(--panel); padding: 16px; overflow: auto; }
    section { margin-bottom: 22px; }
    h2 { margin: 0 0 10px; font-size: 13px; color: var(--muted); font-weight: 700; text-transform: uppercase; letter-spacing: 0; }
    a.task { display: block; color: var(--text); text-decoration: none; border: 1px solid var(--line); border-radius: 6px; padding: 10px; margin-bottom: 8px; background: #111619; }
    a.task:hover, a.task.active { border-color: var(--accent); color: #ecfffb; }
    .meta { color: var(--muted); font-size: 12px; margin-top: 4px; overflow-wrap: anywhere; }
    .empty { color: var(--muted); font-size: 13px; padding: 8px 0; }
    #log-output { margin: 0; padding: 18px; white-space: pre-wrap; overflow: auto; height: calc(100vh - 61px); font-size: 13px; line-height: 1.55; }
    #status { color: var(--muted); font-size: 13px; }
    @media (max-width: 760px) { main { grid-template-columns: 1fr; } aside { border-right: 0; border-bottom: 1px solid var(--line); max-height: 42vh; } #log-output { height: auto; min-height: 58vh; } }
  </style>
</head>
<body>
  <header>
    <h1><a href='/logs/stream'>Deployment Logs</a></h1>
    <div id="status">connecting</div>
  </header>
  <main>
    <aside>
      <section><h2>Running</h2><div id="running-links" class="task-list"></div></section>
      <section><h2>Queued</h2><div id="queued-links" class="task-list"></div></section>
      <section><h2>History</h2><div id="task-links" class="task-list"></div></section>
    </aside>
    <pre id="log-output"></pre>
  </main>
  <script>
    let currentSnapshot = __INITIAL_SNAPSHOT__;
    const statusEl = document.getElementById("status");
    const logEl = document.getElementById("log-output");
    let source = null;
    let selectedTask = new URLSearchParams(location.search).get("task_id") || "";

    function connect(taskId = "") {
      if (source) source.close();
      selectedTask = taskId;
      logEl.textContent = "";
      renderSnapshot(currentSnapshot);
      const runningTask = taskId ? findTask(currentSnapshot.running || [], taskId) : latestRunningTask();
      if (runningTask) {
        connectEvents(taskId);
      } else {
        fetchLog(taskId);
      }
    }

    function connectEvents(taskId = "") {
      const url = taskId ? `/logs/stream?events=1&task_id=${encodeURIComponent(taskId)}` : "/logs/stream?events=1";
      statusEl.textContent = taskId ? `task ${taskId}` : "latest";
      source = new EventSource(url);
      const currentSource = source;
      source.addEventListener("snapshot", event => {
        if (currentSource !== source) return;
        currentSnapshot = JSON.parse(event.data);
        renderSnapshot(currentSnapshot);
      });
      source.addEventListener("log", event => {
        if (currentSource !== source) return;
        const payload = JSON.parse(event.data);
        logEl.textContent += payload.line + "\\n";
        logEl.scrollTop = logEl.scrollHeight;
      });
      source.addEventListener("end", () => {
        if (currentSource !== source) return;
        statusEl.textContent = "finished";
        source.close();
        source = null;
      });
      source.onerror = () => {
        if (currentSource !== source) return;
        statusEl.textContent = "disconnected";
      };
    }

    async function fetchLog(taskId = "") {
      const url = taskId ? `/logs/stream?events=1&task_id=${encodeURIComponent(taskId)}` : "/logs/stream?events=1";
      statusEl.textContent = taskId ? `task ${taskId}` : "latest";
      try {
        const response = await fetch(url);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        logEl.textContent = await response.text();
        statusEl.textContent = "finished";
        logEl.scrollTop = logEl.scrollHeight;
      } catch (error) {
        statusEl.textContent = "disconnected";
        logEl.textContent = String(error);
      }
    }

    function renderSnapshot(snapshot) {
      renderList("running-links", snapshot.running || [], true);
      renderList("queued-links", snapshot.queued || [], false);
      renderList("task-links", snapshot.finished_recent || [], true);
    }

    function renderList(id, tasks, clickable) {
      const target = document.getElementById(id);
      target.innerHTML = "";
      if (!tasks.length) {
        target.innerHTML = '<div class="empty">none</div>';
        return;
      }
      for (const task of tasks) {
        const link = document.createElement(clickable ? "a" : "div");
        link.className = "task" + (task.id === selectedTask ? " active" : "");
        if (clickable) {
          link.href = `/logs/stream?task_id=${encodeURIComponent(task.id)}`;
          link.addEventListener("click", event => {
            event.preventDefault();
            history.replaceState(null, "", link.href);
            connect(task.id);
          });
        }
        link.innerHTML = `<strong>${escapeHtml(task.project || "unknown")}</strong> <span class="meta">${escapeHtml(task.status || "")}</span><div class="meta">${escapeHtml(task.id || "")}</div><div class="meta">${escapeHtml(task.finished_at || "")}</div>`;
        target.appendChild(link);
      }
    }

    function findTask(tasks, taskId) {
      return tasks.find(task => task.id === taskId) || null;
    }

    function latestRunningTask() {
      const running = currentSnapshot.running || [];
      return running.length ? running[running.length - 1] : null;
    }

    function escapeHtml(value) {
      return String(value ?? "").replace(/[&<>"']/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));
    }

    connect(selectedTask);
  </script>
</body>
</html>"""
    return html.replace("__INITIAL_SNAPSHOT__", initial_snapshot)
