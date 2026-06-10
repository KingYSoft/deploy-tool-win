import runpy


def test_package_module_execution_delegates_to_cli_main(monkeypatch):
    called = False

    def fake_main():
        nonlocal called
        called = True

    monkeypatch.setattr("webhook_deployer.cli.main", fake_main)

    runpy.run_module("webhook_deployer", run_name="__main__")

    assert called
