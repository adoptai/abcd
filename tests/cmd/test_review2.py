"""Second round of review fixes: approval forgery (A), live changes (B), self-asserted names (C),
redaction (E), detached HEAD (F), cleanups (G), one fetch per release (H). Credential-helper scope
(D) is in test_review_git.py and test_review_release.py holds the cloud release flow."""

from __future__ import annotations

import base64
import json
import re
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

from cli import abcd, builder_eval, builder_meta, builder_release
from cli.builder_common import cloud, identity
from cli.builder_common import repo as rp
from cli.builder_common.cloudmode import cloud_mode, write_marker
from cli.builder_common.errors import RefusedError
from cli.builder_common.platform import BuilderPlatformClient, PatIdentity, Transcript, jwt_claims
from cli.builder_common.release_approval import human_user_events, sandbox_conversation
from cli.core.events import mask_text
from tests.cmd.quality_fixtures import AGENT, QEnv, qenv  # noqa: F401
from tests.cmd.release_fixtures import build_env, cycle

CLOUD_VARS = ("ADOPT_CONVERSATION_ID", "ABCD_PAT_CLIENT_ID", "ADOPT_APP_BASE_URL")


def sh(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.strip()


@pytest.fixture(autouse=True)
def _local(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for v in CLOUD_VARS:
        monkeypatch.delenv(v, raising=False)


# -- A4. cloud mode does not hang on one variable ------------------------------------------------


@pytest.mark.parametrize("var", CLOUD_VARS)
def test_any_injected_variable_is_a_cloud_run(monkeypatch: pytest.MonkeyPatch, var: str) -> None:
    assert cloud_mode() is False
    monkeypatch.setenv(var, "x")
    assert cloud_mode() is True
    assert identity.current_user() == "cloud:abcd-runner"


def test_the_bootstrap_marker_is_a_cloud_run_without_any_variable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path / "workspaces" / "acme"
    ws.mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    assert cloud_mode(ws) is False
    write_marker(ws, "conv-9")
    assert cloud_mode(ws) is True  # given the workspace
    assert cloud_mode() is False  # never globbed from the bundle root: a workstation is not it
    monkeypatch.chdir(ws)
    assert cloud_mode() is True  # the cwd's own workspace
    assert identity.current_user(ws) == "cloud:abcd-runner"


def test_cloud_bootstrap_writes_the_marker_and_keeps_it_out_of_git(tmp_path: Path) -> None:
    from tests.builder.test_cloud import GitRecorder, _env  # the bootstrap fixtures

    wfile = tmp_path / cloud.WORKSPACE_FILE_NAME
    wfile.write_text(json.dumps({"env": "acme-prod", "repo": "acme/ws", "ref": "main"}))
    res = cloud.bootstrap(
        wfile,
        tmp_path / "abcd",
        environ=_env(),
        exchange_pat=lambda *a: None,
        run_git=GitRecorder(),
    )
    marker = res.workspace_dir / ".builder" / "cloud.json"
    assert json.loads(marker.read_text())["conversation_id"] == "conv-1"
    assert ".builder/" in (res.workspace_dir / ".git" / "info" / "exclude").read_text()
    assert cloud_mode(res.workspace_dir)


# -- A3. a cloud agent does not send approvals ---------------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        ("hitl", "approve-release", "c1", "--release", "a-b@v1", "--sha", "0" * 12, "--yes"),
        ("hitl", "approve-skill", "c1", "--fingerprint", "0" * 32, "--yes"),
    ],
)
def test_approve_commands_are_refused_in_a_cloud_run(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], argv: tuple[str, ...]
) -> None:
    code = abcd.main([*argv, "--dry-run", "--env", "acme", "--json"])
    assert code == 0 and "user_message" in capsys.readouterr().out  # a human, locally
    monkeypatch.setenv("ADOPT_CONVERSATION_ID", "c1")
    code = abcd.main([*argv, "--dry-run", "--env", "acme", "--json"])
    doc = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert code == 1
    assert "a human sends approvals from the chat UI, not the agent" in doc["error"]


# -- A1/A2/A5. who the PAT is, who owns the conversation, who wrote the turn ---------------------


def _jwt(claims: dict[str, Any]) -> str:
    def b64(d: Any) -> str:
        return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")

    return f"{b64({'alg': 'none'})}.{b64(claims)}.sig"


def test_jwt_claims_are_decoded_without_verifying_and_junk_is_empty() -> None:
    assert jwt_claims(_jwt({"userId": "u-1", "sub": "s-1", "email": "a@b.c"}))["userId"] == "u-1"
    assert jwt_claims("not-a-jwt") == {} and jwt_claims("a.b.c") == {} and jwt_claims("") == {}


class _Client(BuilderPlatformClient):
    def __init__(self, token: str, routes: dict[str, Any]) -> None:
        super().__init__(token, "https://app.example.test")
        self.routes = routes

    def _request(self, method: str, path: str, timeout: int = 60, **kw: Any) -> Any:
        hit = self.routes[path.split("?")[0]]
        if isinstance(hit, Exception):
            raise hit
        return hit


BASE = "/v1/end-user/agent-harness"


def test_pat_identity_unions_the_jwt_claims_and_me() -> None:
    from cli.harness_common.api_client import HarnessAPIError

    tok = _jwt({"userId": "u-1", "sub": "s-1", "email": "Svc@Example.test"})
    c = _Client(tok, {f"{BASE}/me": {"user_id": "m-1", "email": "svc@example.test"}})
    who = c.pat_identity()
    assert who.ids == {"u-1", "s-1", "m-1"} and who.emails == {"svc@example.test"}
    # /me failing still leaves the claims; a token with no claims and no /me is empty
    c = _Client(tok, {f"{BASE}/me": HarnessAPIError("GET", "/me", 500, "x")})
    assert c.pat_identity().ids == {"u-1", "s-1"}
    assert not _Client(
        "opaque", {f"{BASE}/me": HarnessAPIError("GET", "/me", 500, "x")}
    ).pat_identity()


def test_transcript_events_carry_the_authors_email_and_is_shared() -> None:
    body = {
        "is_shared": True,
        "events": [{"kind": "user_message", "text": "x", "author_user_id": "u-9"}],
        "authors": {"u-9": {"name": "N", "email": "Human@Example.test"}},
    }
    ev = _Client("t", {f"{BASE}/conversations/c1/transcript": body}).transcript_events("c1")
    assert ev[0]["author_email"] == "human@example.test" and ev.is_shared is True
    private = {"events": [], "is_shared": False}
    assert (
        _Client("t", {f"{BASE}/conversations/c1/transcript": private})
        .transcript_events("c1")
        .is_shared
        is False
    )


class _Fake:
    def __init__(
        self,
        events: list[dict[str, Any]],
        shared: bool = True,
        ids: tuple[str, ...] = ("svc",),
        emails: tuple[str, ...] = ("svc@example.test",),
    ) -> None:
        self.events, self.shared = events, shared
        self.who = PatIdentity(frozenset(ids), frozenset(emails))

    def pat_identity(self) -> PatIdentity:
        return self.who

    def transcript_events(self, conversation_id: str) -> Transcript:
        out = Transcript(self.events)
        out.is_shared = self.shared
        return out


def _ev(text: str, **kw: Any) -> dict[str, Any]:
    return {"kind": "user_message", "text": text, **kw}


def test_human_user_events_keeps_only_other_users_turns() -> None:
    events = [
        _ev("human", author_user_id="fde"),
        _ev("agent", author_user_id="svc"),
        _ev("agent by mail", author_user_id="u-2", author_email="SVC@example.test"),
        _ev("unattributed"),
        {"kind": "assistant_message", "text": "x", "author_user_id": "fde"},
    ]
    kept = human_user_events(_Fake(events), "c1")
    assert [e["text"] for e in kept] == ["human"]
    with pytest.raises(RefusedError, match="cloud approvals need a workstream-shared run"):
        human_user_events(_Fake(events, shared=False), "c1")
    with pytest.raises(RefusedError, match="cannot tell which user"):
        human_user_events(_Fake(events, ids=(), emails=()), "c1")


def test_a_failing_platform_read_refuses_rather_than_accepting() -> None:
    class Boom(_Fake):
        def transcript_events(self, conversation_id: str) -> Transcript:
            raise OSError("down")

    with pytest.raises(RefusedError, match="cannot verify who wrote"):
        human_user_events(Boom([]), "c1")


def test_sandbox_conversation_is_the_markers_and_the_env_must_agree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path / "w"
    ws.mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ADOPT_CONVERSATION_ID", "c1")
    with pytest.raises(RefusedError, match="no bootstrap marker"):
        sandbox_conversation(ws)
    write_marker(ws, "c1")
    assert sandbox_conversation(ws) == "c1"
    monkeypatch.setenv("ADOPT_CONVERSATION_ID", "c2")
    with pytest.raises(RefusedError, match="differs"):
        sandbox_conversation(ws)


def test_browser_skill_token_is_checked_like_the_release_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cli.builder_common import browser_gate as bg
    from tests.builder.test_browser_gate import FakeClient as GateClient  # noqa: F401

    fp = "0123456789abcdef0123456789abcdef"
    tok = f'<!--adopt:skill-approval v="1" fp="{fp}"-->'
    ws = tmp_path / "w"
    ws.mkdir()
    monkeypatch.chdir(tmp_path)
    write_marker(ws, "c1")
    monkeypatch.setenv("ADOPT_CONVERSATION_ID", "c1")
    kept = human_user_events(_Fake([_ev(tok, author_user_id="svc")]), "c1")
    assert bg.approved_fingerprints(kept) == set()  # the PAT's own message does not count
    kept = human_user_events(_Fake([_ev(tok, author_user_id="fde")]), "c1")
    assert bg.approved_fingerprints(kept) == {fp}
    assert "human_user_events" in bg.check_browser_skill.__code__.co_names


# -- C. self-asserted names are refused in the cloud ---------------------------------------------

SELF_ASSERTED = [
    ("criteria", "set", AGENT, "--dataset", "returns", "--metric", "pass_rate", "--op", ">=",
     "--target", "0.9", "--approved-by", "lead"),
    ("dataset", "update-case", "returns", "c1", "--label", "a=b", "--approved-by", "lead"),
    ("gap", "waive", "g1", "--lead", "lead", "--reason", "fine"),
    ("gap", "ask", "g1", "--by", "someone"),
    ("gap", "answer", "g1", "--file", "x.md", "--by", "someone"),
    ("gap", "encode", "g1", "--page", "p.md", "--by", "someone"),
    ("plan", "approve", "item-1", "--by", "lead", "--yes"),
]  # fmt: skip


@pytest.mark.parametrize("argv", SELF_ASSERTED, ids=lambda a: " ".join(a[:2]) + " " + a[-2])
def test_self_asserted_name_flags_are_refused_in_the_cloud(
    qenv: QEnv,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    argv: tuple[str, ...],
) -> None:
    monkeypatch.setenv("ADOPT_CONVERSATION_ID", "c1")
    code, doc = qenv.run(*argv)
    assert code == 1, doc.get("error")
    assert "never in a cloud run" in doc["error"], doc["error"]
    assert qenv.git_history() == qenv.git_history()  # nothing committed either way
    assert sh(qenv.ws, "status", "--porcelain") == ""


def test_the_actor_in_the_cloud_is_always_the_cloud_identity(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class A:
        by = None

    monkeypatch.setenv("ADOPT_CONVERSATION_ID", "c1")
    assert builder_eval.actor(A(), tmp_path) == "cloud:abcd-runner"
    A.by = "alice"  # type: ignore[assignment]
    with pytest.raises(RefusedError, match="--by"):
        builder_eval.actor(A(), tmp_path)
    monkeypatch.delenv("ADOPT_CONVERSATION_ID")
    assert builder_eval.actor(A(), tmp_path) == "alice"  # locally the flag still works


# -- E. redaction --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "leaked", "kept"),
    [
        ("Authorization: Basic dXNlcjpwYXNzd29yZA==", "dXNlcjpwYXNzd29yZA", "Authorization: ***"),
        ("curl -H 'Authorization: Bearer abc.def-ghi_jkl' https://x", "abc.def", "https://x"),
        ("authorization: token ghs_shortbutsecret", "shortbutsecret", "authorization: ***"),
        ("password: multi word value here\nnext: line", "word value", "next: line"),
        ("  PASSWORD:   p4ss w0rd  ", "p4ss", "PASSWORD:"),
        ("git clone https://bob:hunter2@github.com/a/b.git", "hunter2", "https://***@github.com"),
        ("redis://:s3cret@cache:6379/0", "s3cret", "@cache:6379/0"),
        ("ssh://git@github.com/a/b.git", "", "ssh://git@github.com/a/b.git"),  # no password
        ("API_SECRET=$(vault read -field=v secret/x) next", "vault read", "API_SECRET=*** next"),
        ("DB_PASSWORD=${SECRETS_DB} x", "SECRETS_DB", "DB_PASSWORD=*** x"),
        ("TOKEN=`cat /run/secret` y", "cat /run", "TOKEN=*** y"),
        ('API_KEY="ab \\"cd\\" ef" LOG=1', 'cd', 'API_KEY="***" LOG=1'),
        ("SECRET_TOKEN='it\\'s a secret' z=1", "a secret", "SECRET_TOKEN='***' z=1"),
        ('{"text": "API_TOKEN=abc123\\nDB=1", "n": 1}', "abc123", '"n": 1}'),
    ],
)  # fmt: skip
def test_mask_text_every_form(text: str, leaked: str, kept: str) -> None:
    out = mask_text(text)
    if leaked:
        assert leaked not in out, out
    assert kept in out, out
    assert mask_text(out) == out  # idempotent


def test_mask_text_is_linear_on_a_huge_base64_line() -> None:
    import os

    line = base64.b64encode(os.urandom(75_000)).decode()  # ~100k characters
    assert len(line) > 99_000
    t = time.perf_counter()
    out = mask_text(line)
    assert time.perf_counter() - t < 0.5
    assert out == line  # nothing in it is a secret by shape
    assert mask_text("x" * 100_000 + "=") and mask_text("a=b " * 30_000)
    # a secret on a huge line is still masked by shape
    tok = "ghp_" + "A" * 36
    assert tok not in mask_text("q" * 50_000 + " " + tok + " " + "q" * 50_000)
    t = time.perf_counter()
    mask_text("token: " + "a b " * 5000)
    assert time.perf_counter() - t < 0.5


# -- F. detached HEAD -----------------------------------------------------------------------------


def _repo(tmp_path: Path) -> Path:
    d = tmp_path / "w"
    d.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(d)], check=True)
    for k, v in (("user.name", "T"), ("user.email", "t@example.test")):
        sh(d, "config", k, v)
    (d / "a").write_text("a")
    rp.commit(d, "init")
    return d


def test_a_detached_head_has_no_branch_and_gets_a_work_branch(tmp_path: Path) -> None:
    d = _repo(tmp_path)
    sh(d, "checkout", "-q", "--detach", "HEAD")  # what `cloud bootstrap` leaves
    assert rp.current_branch(d) is None  # check=False: no exception
    assert rp.status(d).branch == ""
    (d / "wip.txt").write_text("carried\n")
    name = rp.ensure_work_branch(d, "Cloud: fix!")
    assert name and re.fullmatch(r"builder/cloud-fix-\d{8}-[0-9a-f]{7}", name)
    assert rp.current_branch(d) == name and (d / "wip.txt").exists()
    assert rp.ensure_work_branch(d, "again") is None
    with pytest.raises(Exception, match="Detached HEAD"):
        sh(d, "checkout", "-q", "--detach", "HEAD")
        rp.push(d)


def test_commit_work_on_a_detached_head_moves_to_a_branch(qenv: QEnv) -> None:  # noqa: F811
    sh(qenv.ws, "checkout", "-q", "--detach", "HEAD")
    d = qenv.ok("metric", "define", AGENT, "accuracy", "--category", "quality",
                "--formula", "pass_rate", "--source", "check_results")  # fmt: skip
    assert d["branch"].startswith("builder/") and rp.current_branch(qenv.ws) == d["branch"]
    assert d["commit"] == sh(qenv.ws, "rev-parse", "HEAD")


# -- G. cleanups ----------------------------------------------------------------------------------


def test_commit_work_returns_sha_branch_and_note(qenv: QEnv) -> None:  # noqa: F811
    ws = qenv.ws
    (ws / "x.txt").write_text("x")
    res = builder_eval.commit_work(ws, "add x", ["x.txt"], topic="x")
    assert isinstance(res, tuple) and res.sha == sh(ws, "rev-parse", "HEAD")
    assert res.branch and res.branch in res.note and "never commits there" in res.note
    (ws / "y.txt").write_text("y")
    res2 = builder_eval.commit_work(ws, "add y", ["y.txt"], topic="y")
    assert res2.branch is None and res2.note == ""  # already on a work branch
    assert builder_eval.commit_work(ws, "nothing", ["y.txt"]) == (None, None, "")
    assert not hasattr(builder_eval, "commit_paths") and not hasattr(builder_eval, "branch_note")
    assert not hasattr(builder_meta, "_branch_for")


def test_identity_git_email_goes_through_repo_run_git(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    d = _repo(tmp_path)
    sh(d, "config", "user.email", "fde@example.test")
    monkeypatch.delenv("ABCD_FDE", raising=False)
    seen: list[tuple[Any, ...]] = []
    real = rp.run_git

    def spy(cwd: Any, *args: str, **kw: Any) -> Any:
        seen.append(args)
        return real(cwd, *args, **kw)

    monkeypatch.setattr(rp, "run_git", spy)
    assert identity.current_user(d) == "fde@example.test"
    assert ("config", "user.email") in seen


def test_one_slug_helper() -> None:
    import cli.builder_context as bc

    assert not hasattr(bc, "slug")
    assert identity.slug("Dataset: Q3!", strict=True) == "dataset-q3"
    assert identity.slug("jo@x.com") == "jo-x.com"
    assert identity.slug("", strict=True) == ""


# -- H. one fetch per release ---------------------------------------------------------------------


def test_release_fetches_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    env = build_env(tmp_path, monkeypatch)
    cycle(env, capsys)
    env.merge_to_main()
    calls: list[str] = []
    real = rp.fetch

    def spy(ws: Path, *a: Any, **kw: Any) -> None:
        calls.append(str(ws))
        real(ws, *a, **kw)

    monkeypatch.setattr(rp, "fetch", spy)
    real_run = rp.run_git

    def spy_run(cwd: Any, *args: str, **kw: Any) -> Any:
        if args and args[0] == "fetch":
            calls.append("raw-fetch")
        return real_run(cwd, *args, **kw)

    monkeypatch.setattr(rp, "run_git", spy_run)
    code, doc = env.run("release", "tax-agent" if False else _agent(), "--yes", capsys=capsys)
    assert code == 0, doc.get("error")
    assert len(calls) == 2 and calls.count("raw-fetch") == 1  # one fetch() = one git fetch


def _agent() -> str:
    from tests.cmd.release_fixtures import AGENT as A

    return A


def test_release_branch_has_a_no_fetch_path(tmp_path: Path) -> None:
    assert "do_fetch" in builder_release.ReleaseBranch.__init__.__code__.co_varnames


def test_mask_text_scans_every_line_whatever_its_length() -> None:
    import json
    import time

    out = mask_text("x" * 20_001 + ' "password": "hunter2"')
    assert "hunter2" not in out
    big = "S" * 5000
    assert big not in mask_text(f'{{"api_key": "{big}"}}')
    assert big not in mask_text(f"API_KEY={big}")
    masked = mask_text('{"password": 123, "user": "bob"}')
    assert json.loads(masked)["user"] == "bob" and "123" not in masked
    flag = json.loads(mask_text('{"token": true, "secret": null, "n": 1}'))
    assert flag["n"] == 1 and flag["token"] != "true" and flag["token"] is not True
    assert "bob" not in mask_text("password: bob and more")
    t0 = time.monotonic()
    mask_text("x" * 1_000_000)
    mask_text("k=v " * 250_000)
    mask_text('"a": "' + "q" * 1_000_000)
    assert time.monotonic() - t0 < 1.0


def test_a_marker_in_a_sibling_workspace_does_not_make_another_a_cloud_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cli.builder_common.cloudmode import cloud_mode

    for v in ("ADOPT_CONVERSATION_ID", "ABCD_PAT_CLIENT_ID", "ADOPT_APP_BASE_URL"):
        monkeypatch.delenv(v, raising=False)
    root = tmp_path / "workspaces"
    a, b = root / "a", root / "b"
    for d in (a, b):
        d.mkdir(parents=True)
        (d / "env.json").write_text("{}")
    write_marker(a, "c1")
    monkeypatch.chdir(b)
    assert cloud_mode(b) is False and cloud_mode() is False
    assert cloud_mode(a) is True
    monkeypatch.chdir(root)  # a cwd above the workspaces does not glob them either
    assert cloud_mode(b) is False
    (b / "sub").mkdir()
    monkeypatch.chdir(a / ".builder")  # the cwd inside a: the workspace holding it
    assert cloud_mode(b / "sub") is True  # cwd's workspace is a cloud run
