"""An agent token cannot call /v1/org-db/* (the BFF answers 403), so `agent push` in a
platform-only cloud workspace must treat the missing bld_agents lookup as "no row", the same as a
workspace without bld_* tables."""

from types import SimpleNamespace

import pytest

from cli.builder_agent import agent_row, recorded_process_ids
from cli.harness_common.api_client import HarnessAPIError


def _work(exc: Exception):
    def find_rows(*_a, **_k):
        raise exc

    return SimpleNamespace(svc=SimpleNamespace(rows=SimpleNamespace(find_rows=find_rows)), cfg={})


def test_agent_token_403_on_org_db_means_no_agent_row() -> None:
    exc = HarnessAPIError(
        "GET",
        "http://x/v1/org-db/tables",
        403,
        '{"detail":"Agent tokens are not accepted on this route"}',
    )
    w = _work(exc)
    assert agent_row(w, "a") == {}
    assert recorded_process_ids(w, "a") == (None, None)


def test_other_platform_errors_still_surface() -> None:
    with pytest.raises(HarnessAPIError):
        agent_row(_work(HarnessAPIError("GET", "http://x/v1/org-db/tables", 500, "boom")), "a")


def test_agent_token_403_on_write_grants_reads_as_no_grants(monkeypatch) -> None:
    from cli.builder_common.platform import BuilderPlatformClient

    c = BuilderPlatformClient("tok", "http://x")
    refused = HarnessAPIError(
        "GET", "http://x/p", 403, '{"detail":"Agent tokens are not accepted on this route"}'
    )

    def boom(*_a, **_k):
        raise refused

    monkeypatch.setattr(c, "_request", boom)
    assert c.get_write_workstreams("p1") == {}
    monkeypatch.setattr(
        c,
        "_request",
        lambda *a, **k: (_ for _ in ()).throw(HarnessAPIError("GET", "u", 403, "nope")),
    )
    with pytest.raises(HarnessAPIError):
        c.get_write_workstreams("p1")


def test_a_403_that_is_not_the_agent_token_refusal_still_surfaces() -> None:
    """A person's credential refused on org-db is a real permission problem, not 'no row'."""
    exc = HarnessAPIError("GET", "http://x/v1/org-db/tables", 403, '{"detail":"Forbidden"}')
    with pytest.raises(HarnessAPIError):
        agent_row(_work(exc), "a")
