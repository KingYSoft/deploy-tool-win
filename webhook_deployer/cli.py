from __future__ import annotations

import argparse

import uvicorn

from .app import create_app
from .config import load_config


def main() -> None:
    # 命令行只负责加载配置和启动 ASGI 服务，部署逻辑由 FastAPI 应用处理。
    parser = argparse.ArgumentParser(description="Run the GitHub webhook deployer.")
    parser.add_argument("--config", default="config.yaml", help="Path to YAML configuration file.")
    parser.add_argument("--host", default=None, help="Override listen host.")
    parser.add_argument("--port", type=int, default=None, help="Override listen port.")
    args = parser.parse_args()

    config = load_config(args.config)
    app = create_app(config)
    uvicorn.run(
        app,
        host=args.host or config.server.host,
        port=args.port or config.server.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
