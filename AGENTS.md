# Repository Guidelines

## Project Structure & Module Organization
`webhook_deployer/` contains the Python package for the FastAPI app, CLI entrypoint, configuration loading, webhook security, deployment, sync, queue, and logging behavior. `tests/` contains pytest coverage for those modules using `test_*.py` files. `config.example.yaml` is the safe configuration template for local or server setup; copy it to `config.yaml` for real deployments. `README.md` documents Windows service installation and operational usage.

## Build, Test, and Development Commands
Run `python -m pip install -e .[test]` to install the package in editable mode with test dependencies. Start the service locally with `python -m webhook_deployer --config config.yaml`; use `--host` or `--port` to override configured server values. Run `pytest` to execute the configured test suite under `tests/`. After starting the app, check readiness with `curl http://localhost:9000/health`.

## Coding Style & Naming Conventions
Use Python 3.11+ syntax, four-space indentation, and type hints for public functions and data structures. Follow the existing dataclass-based configuration style in `webhook_deployer/config.py`. Keep modules focused on their current responsibilities: config parsing, security validation, deployment orchestration, directory sync, queueing, logging, and app routing. Name test modules `test_<behavior>.py` and test functions `test_<expected_behavior>`.

## Testing Guidelines
Use pytest and pytest-asyncio for synchronous and async behavior. Add or update tests near the behavior changed, especially for webhook validation, config parsing, queue ordering, deployment decisions, and protected sync paths. Prefer temporary directories, fake deployment runners, explicit config objects, and isolated payloads instead of touching real deployment directories or services.

## Commit & Pull Request Guidelines
The git history is minimal, so use short imperative commit subjects such as `Add queue test` or `Fix webhook signature handling`. Pull requests should include a concise behavior summary, any configuration impact, linked issues when available, and the exact tests run. Include screenshots only when changing the log streaming HTML page or other visible UI.

## Security & Configuration Tips
Do not commit real `config.yaml`, webhook secrets, production server paths, generated logs, or service credentials. Keep reusable examples in `config.example.yaml`. Treat `publish_dir`, `source_dir`, preserved files, and preserved directories carefully because mistakes can affect live deployment output.
