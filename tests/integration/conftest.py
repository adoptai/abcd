"""Fixtures for the offline end-to-end tests: a fake BFF, an ``acme`` workspace and CLI runners."""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from cli.core.context import (  # noqa: E402
    WORKSPACES_ENV_VAR,
    apply_workspaces_override,
    set_workspaces_dir,
)
from cli.wdl_common import workspace_manager as wm  # noqa: E402
from tests.integration.fake_bff import (  # noqa: E402
    DEFAULT_CLIENT_ID,
    DEFAULT_SECRET,
    FakeBFFServer,
)

_ENV_VARS = (
    "ADOPT_WEBUI_ENDPOINT",
    "ADOPT_HARNESS_PAT_CLIENT_ID",
    "ADOPT_HARNESS_PAT_SECRET",
    "ADOPT_HARNESS_BEARER_TOKEN",
)


@dataclass
class CLIResult:
    code: int
    out: str
    err: str

    @property
    def json(self) -> Any:
        """The single ``--json`` document on stdout."""
        return json.loads(self.out.strip().splitlines()[-1])

    @property
    def text(self) -> str:
        return self.out + self.err


@pytest.fixture(autouse=True)
def ws_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """An isolated workspaces dir wired in through ABCD_WORKSPACES_DIR (restored afterwards)."""
    original = wm.WORKSPACES_DIR
    target = tmp_path / "workspaces"
    target.mkdir()
    monkeypatch.setenv(WORKSPACES_ENV_VAR, str(target))
    for name in _ENV_VARS:  # load_dotenv(override=True) sets these; monkeypatch undoes it
        monkeypatch.setenv(name, "x")
        monkeypatch.delenv(name)
    apply_workspaces_override()
    yield target
    monkeypatch.delenv(WORKSPACES_ENV_VAR, raising=False)
    set_workspaces_dir(original)


COVERED_ROUTES: set[str] = set()


@pytest.fixture
def bff() -> Iterator[FakeBFFServer]:
    with FakeBFFServer() as server:
        yield server
        COVERED_ROUTES.update(server.route_keys())


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """``FAKE_BFF_COVERAGE=<file>`` writes which fake routes the suite served (for the README)."""
    target = os.environ.get("FAKE_BFF_COVERAGE")
    if target:
        from tests.integration.fake_bff import route_table

        Path(target).write_text(
            json.dumps({"routes": route_table(), "served": sorted(COVERED_ROUTES)}, indent=2)
        )


def write_env_file(env_dir: Path, url: str, client_id: str, secret: str) -> None:
    """Point a workspace at ``url`` with this PAT. A new PAT drops the cached bearer token."""
    (env_dir / "harness" / ".token_cache.json").unlink(missing_ok=True)
    (env_dir / ".env").write_text(
        f"ADOPT_WEBUI_ENDPOINT={url}\n"
        f"ADOPT_HARNESS_PAT_CLIENT_ID={client_id}\n"
        f"ADOPT_HARNESS_PAT_SECRET={secret}\n"
    )


def run_inproc(*argv: str) -> CLIResult:
    """Run the real CLI in this process: ``cli.abcd.main([...])``."""
    from cli import abcd

    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = abcd.main(list(argv))
        except SystemExit as exc:  # argparse errors
            code = exc.code if isinstance(exc.code, int) else 1
    return CLIResult(code, out.getvalue(), err.getvalue())


def run_subprocess(ws_root: Path, *argv: str, cwd: Path | None = None) -> CLIResult:
    """Run ``python -m cli.abcd`` in a fresh process with a minimal, isolated environment."""
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "PYTHONPATH": str(ROOT),
        "ABCD_WORKSPACES_DIR": str(ws_root),
        "HOME": str(ws_root.parent),
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_AUTHOR_NAME": "Test FDE",
        "GIT_AUTHOR_EMAIL": "fde@example.test",
        "GIT_COMMITTER_NAME": "Test FDE",
        "GIT_COMMITTER_EMAIL": "fde@example.test",
        "GIT_TERMINAL_PROMPT": "0",
    }
    for key in ("PYTHONPYCACHEPREFIX", "TMPDIR"):
        if key in os.environ:
            env[key] = os.environ[key]
    proc = subprocess.run(
        [sys.executable, "-m", "cli.abcd", *argv],
        capture_output=True,
        text=True,
        env=env,
        cwd=cwd or ROOT,
        check=False,
        timeout=120,
    )
    return CLIResult(proc.returncode, proc.stdout, proc.stderr)


@pytest.fixture(autouse=True)
def git_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """Identity and isolation for git calls made by in-process CLI commands."""
    for key, val in {
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_AUTHOR_NAME": "Test FDE",
        "GIT_AUTHOR_EMAIL": "fde@example.test",
        "GIT_COMMITTER_NAME": "Test FDE",
        "GIT_COMMITTER_EMAIL": "fde@example.test",
        "GIT_TERMINAL_PROMPT": "0",
    }.items():
        monkeypatch.setenv(key, val)


@pytest.fixture
def acme(ws_dir: Path, bff: FakeBFFServer) -> Path:
    """The ``acme`` workspace: created by ``abcd ws create``, ``.env`` pointing at the fake BFF."""
    res = run_inproc(
        "ws", "create", "--id", "acme", "--name", "Acme", "--client", "acme", "--target", "staging"
    )
    assert res.code == 0, res.text
    env_dir = ws_dir / "acme"
    write_env_file(env_dir, bff.url, DEFAULT_CLIENT_ID, DEFAULT_SECRET)
    return env_dir


@pytest.fixture
def cli(acme: Path) -> Callable[..., CLIResult]:
    """``cli("skill", "push", ...)``: in-process, ``--env acme`` added."""

    def _run(*argv: str, env: str | None = "acme") -> CLIResult:
        extra = ["--env", env] if env else []
        return run_inproc(*argv, *extra)

    return _run


@pytest.fixture
def fixture_skill(tmp_path: Path) -> Path:
    """A clean, offline skill directory (frontmatter, a script and a reference)."""
    d = tmp_path / "skills" / "invoice-triage"
    (d / "references").mkdir(parents=True)
    (d / "scripts").mkdir()
    (d / "SKILL.md").write_text(
        "---\n"
        "name: invoice-triage\n"
        "description: Sort incoming invoices into approve, hold or reject buckets. Use when the user\n"
        "  asks to triage invoices.\n"
        "output_type: text\n"
        "---\n\n"
        "# Invoice triage\n\n"
        "1. List the invoices in the workstream.\n"
        "2. Read `references/rules.md` and apply it.\n"
        "3. Write the result with `scripts/summarize.py`.\n"
    )
    (d / "references" / "rules.md").write_text("Approve under 500. Hold over 5000.\n")
    (d / "scripts" / "summarize.py").write_text("print('summary')\n")
    return d


@pytest.fixture
def fixture_plugin(tmp_path: Path) -> Path:
    d = tmp_path / "plugins" / "acme-finance"
    (d / ".claude-plugin").mkdir(parents=True)
    (d / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "acme-finance", "version": "1.0.0", "description": "Finance helpers"})
    )
    skill = d / "skills" / "ledger-check"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: ledger-check\ndescription: Check a ledger export for gaps. Use for ledger reviews.\n"
        "---\n\n# Ledger check\n\nRead the export and list gaps.\n"
    )
    (skill / "notes.md").write_text("Gaps are missing sequence numbers.\n")
    return d


GITHUB_URL = "https://github.test/acme/acme.git"

_FAKE_GH = '''#!{python}
"""A fake ``gh``: answers auth status, pr create/list and release create; logs every call."""
import json, os, sys
args = sys.argv[1:]
with open(os.environ["FAKE_GH_LOG"], "a") as fh:
    fh.write(json.dumps(args) + "\\n")
if args[:2] == ["auth", "status"]:
    sys.exit(0)
if args[:2] == ["pr", "create"]:
    print("https://github.test/acme/acme/pull/1")
elif args[:2] == ["pr", "list"]:
    print("[]")
elif args[:2] == ["release", "create"]:
    print("https://github.test/acme/acme/releases/tag/" + args[2])
else:
    sys.stderr.write("fake gh: unsupported " + " ".join(args))
    sys.exit(1)
'''


@dataclass
class FakeGitHub:
    """A local bare repo that stands in for ``https://github.test/acme/acme`` plus a fake ``gh``."""

    bare: Path
    log: Path

    @property
    def url(self) -> str:
        return GITHUB_URL

    def calls(self) -> list[list[str]]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines() if line]


@pytest.fixture
def fake_github(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeGitHub:
    """A GitHub-shaped remote that never leaves the machine.

    git rewrites ``https://github.test/acme/acme.git`` to a local bare repo (``insteadOf``), and
    a fake ``gh`` on PATH answers the pull-request and release calls, so the workspace's
    ``repo_remote`` parses as owner/repo while no network call is possible.
    """
    from tests.integration.helpers import bare_repo

    bare = bare_repo(tmp_path / "github")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    gh = bindir / "gh"
    gh.write_text(_FAKE_GH.format(python=sys.executable))
    gh.chmod(0o755)
    log = tmp_path / "gh-calls.jsonl"
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("FAKE_GH_LOG", str(log))
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", f"url.{bare}.insteadOf")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", GITHUB_URL)
    for key in ("ABCD_GITHUB_TOKEN", "SANDBOX_GITHUB_TOKEN", "GITHUB_TOKEN", "GH_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    return FakeGitHub(bare, log)
