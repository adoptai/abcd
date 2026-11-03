"""``abcd cloud setup --home`` against the fake BFF server."""

# ruff: noqa: F401, F811
from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any

from cli.builder_common.workspace import load_builder_config, update_builder_config
from tests.cmd.meta_fixtures import Env, home_env, meta_env, run_cli

REPO = "example-org/zeta-workspace"


def _home(env: Env, **kw: Any) -> Path:
    ws = env.ws("zeta-home", **kw)
    env.write_env(ws)
    return ws


def _args(*extra: str) -> list[str]:
    return [
        "cloud",
        "setup",
        "--home",
        "--env",
        "zeta-home",
        "--workspace-env",
        "zeta",
        "--repo",
        REPO,
        *extra,
    ]


def _live(env: Env, method: str, suffix: str) -> int:
    assert env.bff is not None
    return sum(1 for r in env.bff.requests if r["method"] == method and r["path"].endswith(suffix))


def _pointer(env: Env) -> dict[str, Any]:
    assert env.bff is not None
    st = env.bff.state
    sids = [s["id"] for s in st.stores.values() if s["workstream_ids"] == [_ws_id(env)]]
    docs = [  # the platform's own empty default store is there too; the pointer is the one file
        d for sid in sids for d in st.documents[sid].values() if d["status"] != "deleted"
    ]
    (doc,) = docs  # exactly one file
    assert doc["relative_path"] == "abcd-workspace.json"
    return json.loads(st.objects[doc["s3_key"]])


def _ws_id(env: Env) -> str:
    assert env.bff is not None
    (w,) = [w for w in env.bff.state.workstreams.values() if w["name"] == "abcd-zeta"]
    return str(w["id"])


def test_dry_run_makes_no_call_and_writes_nothing(home_env: Env) -> None:
    ws = _home(home_env)
    before = (ws / "builder.json").read_text()
    code, doc = run_cli(*_args("--dry-run"))
    assert code == 0 and doc["data"]["dry_run"] is True
    assert home_env.bff is not None and home_env.bff.requests == []  # not even a PAT exchange
    assert (ws / "builder.json").read_text() == before
    plan = doc["data"]["plan"]
    assert plan["workstream"] == "abcd-zeta"
    assert plan["pointer"]["content"] == {"env": "zeta", "repo": REPO, "ref": "main"}
    assert plan["pointer"]["replace"] is True
    assert [s["name"] for s in plan["skills"]] == ["abcd", "abcd-release-gate"]
    assert all(s["tier"] == "org" for s in plan["skills"])
    assert [p["name"] for p in plan["processes"]] == ["abcd-runner", "abcd-nightly", "abcd-weekly"]
    steps = plan["manual_steps"]
    assert {s["name"] for s in steps["vault_secrets"]} >= {
        "SANDBOX_ABCD_PAT_CLIENT_ID",
        "SANDBOX_ABCD_PAT_SECRET",
        "SANDBOX_GITHUB_TOKEN",
    }
    assert [p["process"] for p in steps["pipelines"]] == ["abcd-nightly", "abcd-weekly"]
    assert any("browser" in n for n in steps["notes"])


def test_real_run_creates_everything(home_env: Env) -> None:
    ws = _home(home_env)
    code, doc = run_cli(*_args())
    assert code == 0, doc
    st = home_env.bff.state  # type: ignore[union-attr]
    data = doc["data"]
    assert data["workstream"]["created"] is True
    assert _pointer(home_env) == {"env": "zeta", "repo": REPO, "ref": "main"}
    assert data["pointer"]["state"] == "written"
    # skills: org tier only, with the bundle; never a default skill
    assert set(st.org_skills) == {"abcd", "abcd-release-gate"} and st.default_skills == {}
    assert sorted(st.org_skills["abcd"]["aux"]) == ["abcd-bundle.zip", "bundle.sha256"]
    assert st.org_skills["abcd"]["md"].decode().startswith("---\nname: abcd\n")
    assert {s["state"] for s in data["skills"]} == {"created"}
    # processes
    procs = {p["name"]: p for p in st.processes.values()}
    assert set(procs) == {"abcd-runner", "abcd-nightly", "abcd-weekly"}
    wid = _ws_id(home_env)
    for p in procs.values():
        assert p["is_enabled"] is True
        assert st.process_ws[p["id"]] == [wid]
        assert st.write_ws.get(p["id"], {"workstream_ids": []})["workstream_ids"] == []
        assert {"type": "skill", "source": "org", "ref": "abcd"} in p["components"]
    assert {"type": "skill", "source": "org", "ref": "abcd-release-gate"} in procs["abcd-runner"][
        "components"
    ]
    assert "abcd cloud nightly" in procs["abcd-nightly"]["agent_instructions"]
    # builder.json records the role and ids
    cfg = load_builder_config(ws)
    assert cfg["role"] == "home" and cfg["workstreams"] == {"abcd-zeta": wid}
    # nothing the run did is a pipeline, a vault write or a write outside the home workstream
    assert _live(home_env, "POST", "/v1/pipelines") == 0
    assert not any(r["path"].startswith("/v1/vault") for r in home_env.bff.requests)  # type: ignore[union-attr]
    assert data["manual_steps"]["vault_secrets"]


def test_second_run_changes_nothing(home_env: Env) -> None:
    _home(home_env)
    first_code, first = run_cli(*_args())
    assert first_code == 0
    st = home_env.bff.state  # type: ignore[union-attr]
    snapshot = (
        dict(st.workstreams),
        sorted(st.processes),
        sorted(st.org_skills),
        len(st.stores),
        sum(len(d) for d in st.documents.values()),
    )
    writes_before = len(
        [r for r in home_env.bff.requests if r["method"] in ("POST", "PUT", "PATCH", "DELETE")]  # type: ignore[union-attr]
    )
    code, doc = run_cli(*_args())
    assert code == 0, doc
    data = doc["data"]
    assert data["workstream"]["created"] is False and data["doc_store"]["created"] is False
    assert data["pointer"]["state"] == "unchanged"
    assert {s["state"] for s in data["skills"]} == {"unchanged"}
    assert all(p["created"] is False and p["patched"] == [] for p in data["processes"])
    assert [p["process_id"] for p in data["processes"]] == [
        p["process_id"] for p in first["data"]["processes"]
    ]
    assert snapshot == (
        dict(st.workstreams),
        sorted(st.processes),
        sorted(st.org_skills),
        len(st.stores),
        sum(len(d) for d in st.documents.values()),
    )
    writes_after = [
        r
        for r in home_env.bff.requests  # type: ignore[union-attr]
        if r["method"] in ("POST", "PUT", "PATCH", "DELETE")
    ]
    new = writes_after[writes_before:]
    assert [r["path"] for r in new if "docstore" in r["path"] or "skills" in r["path"]] == [], new
    assert [r for r in new if r["path"].startswith("/v1/org/processes")] == []


def test_changed_pointer_and_edited_process_are_repaired(home_env: Env) -> None:
    _home(home_env)
    assert run_cli(*_args())[0] == 0
    st = home_env.bff.state  # type: ignore[union-attr]
    (nightly,) = [p for p in st.processes.values() if p["name"] == "abcd-nightly"]
    nightly["agent_instructions"] = "edited by hand"
    code, doc = run_cli(*_args("--ref", "release-1"))
    assert code == 0, doc
    assert doc["data"]["pointer"]["state"] == "written"
    assert _pointer(home_env)["ref"] == "release-1"  # replaced, not duplicated
    (patched,) = [p for p in doc["data"]["processes"] if p["patched"]]
    assert patched["name"] == "abcd-nightly" and "agent_instructions" in patched["patched"]
    assert "abcd cloud nightly" in nightly["agent_instructions"]


def test_rerun_keeps_a_disabled_process_disabled(home_env: Env) -> None:
    _home(home_env)
    assert run_cli(*_args())[0] == 0
    st = home_env.bff.state  # type: ignore[union-attr]
    (nightly,) = [p for p in st.processes.values() if p["name"] == "abcd-nightly"]
    nightly["is_enabled"] = False  # an operator turned it off
    code, doc = run_cli(*_args())
    assert code == 0, doc
    assert nightly["is_enabled"] is False
    assert all("is_enabled" not in p["patched"] for p in doc["data"]["processes"])


def test_repo_and_ref_come_from_the_client_workspace(home_env: Env) -> None:
    _home(home_env)
    ws = home_env.ws("zeta", repo_remote="git@github.com:example-org/zeta-ws.git")
    update_builder_config(ws, default_branch="trunk")
    code, doc = run_cli(
        "cloud", "setup", "--home", "--env", "zeta-home", "--workspace-env", "zeta", "--dry-run"
    )
    assert code == 0
    assert doc["data"]["plan"]["pointer"]["content"] == {
        "env": "zeta",
        "repo": "example-org/zeta-ws",
        "ref": "trunk",
    }
    code, doc = run_cli(
        "cloud", "setup", "--home", "--env", "zeta-home", "--workspace-env", "nowhere", "--dry-run"
    )
    assert code == 2 and "--repo" in doc["error"]


def test_refuses_a_client_org_workspace(home_env: Env) -> None:
    ws = _home(home_env, role="client")
    code, doc = run_cli(*_args())
    assert code == 1 and "client org" in doc["error"]
    assert home_env.bff.requests == []  # type: ignore[union-attr]
    assert load_builder_config(ws)["role"] == "client"
    # an older workspace that `builder setup` configured, before the role existed, is refused too
    ws2 = home_env.ws("zeta-old", workstreams={"builder-context": "w1"})
    home_env.write_env(ws2)
    code, doc = run_cli(*[a if a != "zeta-home" else "zeta-old" for a in _args()])
    assert code == 1 and "client org" in doc["error"]
    assert home_env.bff.requests == []  # type: ignore[union-attr]
    assert "role" not in json.loads((ws2 / "builder.json").read_text())


def test_default_tier_name_collision_is_refused(home_env: Env) -> None:
    _home(home_env)
    home_env.bff.state.add_default_skill("abcd")  # type: ignore[union-attr]
    code, doc = run_cli(*_args())
    assert code == 1 and "name taken" in doc["error"]
    assert "abcd" not in home_env.bff.state.org_skills  # type: ignore[union-attr]
    assert home_env.bff.state.processes == {}  # type: ignore[union-attr]


def test_bundle_is_uploaded_with_the_skill(home_env: Env) -> None:
    _home(home_env)
    assert run_cli(*_args())[0] == 0
    raw = home_env.bff.state.org_skills["abcd"]["aux"]["abcd-bundle.zip"]  # type: ignore[union-attr]
    import io

    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        assert z.namelist() == ["pyproject.toml"]


def _variant_bundle(monkeypatch: Any, char: str) -> None:
    """A bundle of exactly the same size as the fixture's, with different content."""
    from cli import builder_home

    def build(out: Path) -> None:
        with zipfile.ZipFile(out, "w") as z:
            z.writestr(
                zipfile.ZipInfo("pyproject.toml", (2020, 1, 1, 0, 0, 0)),
                f"[tool.poetry]\nname = '{char}bcd'\n",
            )

    monkeypatch.setattr(builder_home, "BUNDLE_BUILDER", build)


def test_same_size_bundle_with_other_content_is_uploaded_again(
    home_env: Env, monkeypatch: Any
) -> None:
    _home(home_env)
    assert run_cli(*_args())[0] == 0
    st = home_env.bff.state  # type: ignore[union-attr]
    before = dict(st.org_skills["abcd"]["aux"])
    _variant_bundle(monkeypatch, "x")
    code, doc = run_cli(*_args())
    assert code == 0, doc
    states = {s["name"]: s["state"] for s in doc["data"]["skills"]}
    assert states["abcd"] == "updated"  # the size did not change, the sha256 text did
    after = st.org_skills["abcd"]["aux"]
    assert len(after["abcd-bundle.zip"]) == len(before["abcd-bundle.zip"])
    assert after["bundle.sha256"] != before["bundle.sha256"]
    assert after["abcd-bundle.zip"] != before["abcd-bundle.zip"]
    # and a third run with the same content changes nothing
    code, doc = run_cli(*_args())
    assert {s["name"]: s["state"] for s in doc["data"]["skills"]}["abcd"] == "unchanged"


def test_a_missing_digest_file_counts_as_changed(home_env: Env) -> None:
    _home(home_env)
    assert run_cli(*_args())[0] == 0
    st = home_env.bff.state  # type: ignore[union-attr]
    del st.org_skills["abcd"]["aux"]["bundle.sha256"]  # an older upload had no digest file
    code, doc = run_cli(*_args())
    assert code == 0, doc
    assert {s["name"]: s["state"] for s in doc["data"]["skills"]}["abcd"] == "updated"
    assert "bundle.sha256" in st.org_skills["abcd"]["aux"]
