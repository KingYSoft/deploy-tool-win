# GitHub Webhook Deployer

这是一个适合 Windows 服务器运行的 Python3 GitHub Webhook 部署服务。它可以接收 GitHub `push` 事件，自动发布 Vue3 静态站点，也可以发布 .NET 8 Web API Windows 服务，并提供实时部署日志和发布目录保护能力。

Vue3 项目默认执行：

```powershell
yarn install --frozen-lockfile
yarn build:test
```

如果某个项目使用不同的构建命令，可以在 `config.yaml` 中通过 `build_command` 覆盖。

## 一、服务器准备

在服务器上安装并配置好以下工具：

```text
git
Python 3.11+
yarn
dotnet 8 SDK   # 只有需要部署 .NET 项目时才需要
nssm
```

运行部署服务的 Windows 账号必须有权限：

- 执行 `git`、`yarn`、`dotnet`
- 读取项目源码目录
- 写入发布目录
- 读写日志目录
- 停止和启动配置中的 Windows 服务

## 二、部署脚本到服务器

建议将本项目放到固定目录，例如：

```text
D:\work\code\sjc\python
```

进入目录后安装 Python 依赖：

```powershell
cd D:\work\code\sjc\python
python -m pip install -e .[test]
```

## 三、创建配置文件

复制示例配置：

```powershell
copy config.example.yaml config.yaml
```

编辑 `config.yaml`。Vue3 项目示例：

```yaml
server:
  host: 0.0.0.0
  port: 9000
  log_dir: logs

projects:
  - name: frontend
    repository: 你的组织/你的仓库
    type: vue3
    branches: [main]
    source_dir: D:/sites/frontend-src
    webhook_secret: GitHub里配置的secret
    publish_dir: D:/sites/frontend
    build_dir: dist
    build_command: [yarn, build:test]
    preserve_files:
      - config.js
    preserve_dirs:
      - uploads
```

字段说明：

- `repository`：GitHub 仓库全名，格式通常是 `owner/repo`
- `branches`：允许触发部署的分支
- `source_dir`：服务器上已经 clone 好的源码目录
- `webhook_secret`：GitHub Webhook 中配置的 Secret，必须一致
- `publish_dir`：最终发布目录
- `build_dir`：构建产物目录，Vue3 默认通常是 `dist`
- `build_command`：构建命令数组，Vue3 默认使用 `[yarn, build:test]`

## 四、手动启动测试

先在命令行手动启动，确认配置没有问题：

```powershell
python -m webhook_deployer --config config.yaml
```

健康检查：

```powershell
curl http://localhost:9000/health
```

正常返回：

```json
{"status":"ok"}
```

Webhook 地址：

```text
http://<server>:9000/webhook/github
```

## 五、注册为 Windows 服务

确认手动启动正常后，用 NSSM 注册为 Windows 服务：

```powershell
nssm install GitHubWebhookDeployer D:\work\Python313\python.exe
nssm set GitHubWebhookDeployer AppDirectory D:\work\code\sjc\python
nssm set GitHubWebhookDeployer AppParameters -m webhook_deployer --config D:\work\code\sjc\python\config.yaml
nssm set GitHubWebhookDeployer Start SERVICE_AUTO_START
nssm start GitHubWebhookDeployer
```

注意将 `D:\work\Python313\python.exe` 改成服务器上的真实 Python 路径。

## 六、配置 GitHub Webhook

进入 GitHub 仓库设置页，添加 Webhook：

```text
Payload URL: http://你的服务器IP:9000/webhook/github
Content type: application/json
Secret: config.yaml 里的 webhook_secret
Event: Just the push event
```

如果服务器前面有防火墙、Nginx、IIS 或其他反向代理，需要放行 `9000` 端口，或者将公网地址反向代理到本服务。

## 七、查看部署队列和日志

Webhook 请求成功后会返回 `task_id`。直接在浏览器打开日志页面：

```powershell
start http://localhost:9000/logs/stream
```

页面会显示：

- 当前正在并发部署的任务链接
- 同项目串行等待中的队列任务
- 最近的历史部署任务链接
- 当前选中任务的实时或历史日志

有 2 个或更多项目正在并发部署时，可以点击页面中的任务链接切换查看不同部署日志。当前没有部署任务时，可以点击历史任务链接查看历史日志。

如果需要通过命令行读取 SSE 事件流，使用 `events=1`：

```powershell
curl -N http://localhost:9000/logs/stream?events=1
```

查看指定任务日志：

```powershell
curl -N "http://localhost:9000/logs/stream?events=1&task_id=<task_id>"
```

日志文件、历史日志读取、HTML 页面和 SSE 响应都使用 UTF-8。服务会尽量让部署子进程输出 UTF-8，避免构建日志中的中文乱码。日志时间使用服务器本地时间，不使用 UTC 时间。

## 八、保护发布目录中的文件和文件夹

每个项目都可以配置需要保留的文件和目录：

```yaml
preserve_files:
  - appsettings.Production.json
preserve_dirs:
  - logs
  - uploads
```

这些路径都相对于 `publish_dir`。部署同步时，它们不会被覆盖、删除或清空。为了避免误删服务器文件，配置中的绝对路径和包含 `..` 的路径会被拒绝。
