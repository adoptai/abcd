"""Shared pytest configuration for noui tests."""


def pytest_configure(config):
    config.addinivalue_line("markers", "e2e: end-to-end autopilot tests (need --run-e2e)")


def pytest_addoption(parser):
    parser.addoption(
        "--run-e2e", action="store_true", default=False, help="Run E2E autopilot tests"
    )


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--run-e2e"):
        import pytest

        skip = pytest.mark.skip(reason="Need --run-e2e to run")
        for item in items:
            if "e2e" in item.keywords:
                item.add_marker(skip)
