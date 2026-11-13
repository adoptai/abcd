"""Regression tests for review findings 8 (transcript redaction) and 10 (one identity helper)."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

import cli.builder_events as be
from cli import builder_agent, builder_context, builder_eval
from cli.builder_common import identity
from cli.core.events import mask_text
from tests.cmd.ops_fixtures import OpsEnv, ops_env, run


@pytest.fixture
def ops(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[OpsEnv]:
    with ops_env(tmp_path, monkeypatch) as e:
        yield e


# -- 8. transcript redaction -------------------------------------------------------------------

ENV_DUMP = """\
DATABASE_URL=postgres://app@db.internal/app
API_TOKEN=tok_fake_aaaa_bbbb
export ADOPT_CLIENT_SECRET="s3cr3t value with spaces"
SANDBOX_ABCD_PAT_SECRET='pat-secret-123'
DB_PASSWORD=hunter2
LOG_LEVEL=debug
"""

JSON_CONFIG = """\
{"name": "acme", "api_key": "k-123-456", "auth": {"client_secret": "cs-777", "password": "pw 1"},
 "retries": 3, "token": "abc def", "region": "us-east-1"}
password: yaml-pw-5
note: the key: is not secret here
"""


def test_mask_text_masks_secret_named_pairs_in_every_form() -> None:
    out = mask_text(ENV_DUMP + JSON_CONFIG)
    for leaked in (
        "tok_fake_aaaa_bbbb", "s3cr3t value", "pat-secret-123", "hunter2", "k-123-456",
        "cs-777", "pw 1", "abc def", "yaml-pw-5",
    ):  # fmt: skip
        assert leaked not in out, leaked
    # the key names and the harmless values survive
    for kept in ("API_TOKEN=***", "DB_PASSWORD=***", 'ADOPT_CLIENT_SECRET="***"',
                 '"api_key": "***"', "password: ***", "LOG_LEVEL=debug",
                 "postgres://app@db.internal/app", '"region": "us-east-1"', '"retries": 3',
                 '"name": "acme"'):  # fmt: skip
        assert kept in out, kept


def test_mask_text_still_masks_token_shapes_and_is_idempotent() -> None:
    ghp = "ghp_" + "a" * 30
    text = f"cloned with {ghp}; Authorization: Bearer abcdefghijklmnopqrst"
    once = mask_text(text)
    assert ghp not in once and "abcdefghijklmnopqrst" not in once
    assert mask_text(once) == once


def test_session_end_redacts_a_env_dump_and_json_config(ops: OpsEnv, tmp_path: Path) -> None:
    assert run("events", "session", "start", "--session-id", "s-red")[0] == 0
    t = tmp_path / "t.jsonl"
    t.write_text(json.dumps({"role": "tool", "text": ENV_DUMP}) + "\n" + JSON_CONFIG)
    code, doc = run("events", "session", "end", "--transcript", str(t))
    assert code == 0, doc
    rel = doc["data"]["transcript_path"].removeprefix("builder-ops/")
    body = ops.docs.read("store-1", rel)
    for leaked in ("tok_fake_aaaa_bbbb", "hunter2", "k-123-456", "cs-777", "yaml-pw-5",
                   "s3cr3t value", "pat-secret-123"):  # fmt: skip
        assert leaked not in body
    assert "LOG_LEVEL=debug" in body and "us-east-1" in body


# -- 10. one identity helper --------------------------------------------------------------------


def _git_repo(path: Path, email: str | None) -> Path:
    path.mkdir(exist_ok=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    if email:
        subprocess.run(["git", "config", "user.email", email], cwd=path, check=True)
    return path


def test_identity_order_fde_then_git_email_then_user(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ADOPT_CONVERSATION_ID", raising=False)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", "/dev/null")
    monkeypatch.setenv("USER", "osuser")
    monkeypatch.setattr("getpass.getuser", lambda: "osuser")
    ws = _git_repo(tmp_path / "ws", "dev@example.test")
    monkeypatch.setenv("ABCD_FDE", "fde@example.test")
    assert identity.current_user(ws) == "fde@example.test"
    monkeypatch.delenv("ABCD_FDE")
    assert identity.current_user(ws) == "dev@example.test"
    assert identity.current_user(_git_repo(tmp_path / "bare-ws", None)) == "osuser"
    assert identity.current_user() == "osuser"
    monkeypatch.setenv("ABCD_USER", "old-var")  # no longer consulted
    assert identity.current_user() == "osuser"


def test_cloud_identity_is_cloud_process_and_cannot_be_overridden(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ADOPT_CONVERSATION_ID", "conv-1")
    monkeypatch.setenv("ABCD_FDE", "someone-else")
    assert identity.current_user() == "cloud:abcd-runner"
    monkeypatch.setenv("ABCD_CLOUD_PROCESS", "abcd-nightly")
    assert identity.current_user() == "cloud:abcd-nightly"


def test_slug_is_a_safe_path_segment() -> None:
    assert identity.slug("jo.smith@example.test") == "jo.smith-example.test"
    assert identity.slug("cloud:abcd-runner") == "cloud-abcd-runner"
    assert identity.slug("../../etc/passwd") == "etc-passwd"
    assert identity.slug("  ") == "unknown"


def test_the_three_old_helpers_now_agree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = _git_repo(tmp_path / "ws", "lead@example.test")
    monkeypatch.delenv("ADOPT_CONVERSATION_ID", raising=False)
    monkeypatch.delenv("ABCD_FDE", raising=False)
    monkeypatch.setenv("ABCD_USER", "ignored")
    want = identity.current_user(ws)
    assert want == "lead@example.test"
    assert builder_context.actor(ws) == want
    assert builder_eval.actor(None, ws) == want
    assert builder_agent.current_user is identity.current_user
    assert builder_eval.actor(type("A", (), {"by": "dana"})()) == "dana"  # --by still wins
    monkeypatch.setenv("ADOPT_CONVERSATION_ID", "c-2")
    assert builder_context.actor(ws) == builder_eval.actor(None, ws) == "cloud:abcd-runner"


def test_session_end_works_with_an_email_identity(
    ops: OpsEnv, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ABCD_FDE", "dana.k@example.test")
    assert run("events", "session", "start", "--session-id", "s-mail")[0] == 0
    row = ops.db.find_rows("bld_dev_sessions", "session_id", "s-mail")[0]
    assert row["user_id"] == "dana.k@example.test"
    t = tmp_path / "t.jsonl"
    t.write_text("{}\n")
    code, doc = run("events", "session", "end", "--transcript", str(t))
    assert code == 0, doc
    day = row["started_at"][:10]
    assert doc["data"]["transcript_path"] == (
        f"builder-ops/sessions/dana.k-example.test/{day}/s-mail/transcript.jsonl"
    )
    done = ops.db.find_rows("bld_dev_sessions", "session_id", "s-mail")[0]
    assert done["user_id"] == "dana.k@example.test" and done["ended_at"]


def test_session_end_works_in_cloud_mode(
    ops: OpsEnv, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ADOPT_CONVERSATION_ID", "conv-9")
    assert run("events", "session", "start", "--session-id", "s-cloud")[0] == 0
    row = ops.db.find_rows("bld_dev_sessions", "session_id", "s-cloud")[0]
    assert row["user_id"] == "cloud:abcd-runner"
    t = tmp_path / "t.jsonl"
    t.write_text("{}\n")
    code, doc = run("events", "session", "end", "--transcript", str(t))
    assert code == 0, doc
    assert "/sessions/cloud-abcd-runner/" in doc["data"]["transcript_path"]
    assert be.session_id_for(ops.ws) == "conv-9"  # the session file is gone: falls back to conv
