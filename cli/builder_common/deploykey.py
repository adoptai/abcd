"""GitHub deploy-key auth for cloud runs (PLAN.md section 6).

A repo-scoped deploy key is an alternative to ``GITHUB_TOKEN`` for the sandbox's git calls. It
arrives as the vault secret ``SANDBOX_GITHUB_DEPLOY_KEY`` (the sandbox sees ``GITHUB_DEPLOY_KEY``).
The private key is written to a 0600 file under the sandbox home, never under the workspace and
never into argv, a URL or any output; git reaches it through ``GIT_SSH_COMMAND``. A deploy key
cannot call the GitHub REST API, so pull requests and GitHub releases stay flagged without a token.
"""

from __future__ import annotations

import os
import re
import shlex
from collections.abc import Mapping
from pathlib import Path

from cli.builder_common.errors import UsageError

VAR_DEPLOY_KEY = "GITHUB_DEPLOY_KEY"
KEY_DIR_NAME = ".abcd"
KEY_FILE_NAME = "github_deploy_key"
GIT_SSH_VAR = "GIT_SSH_COMMAND"

_HTTPS_GITHUB = re.compile(r"^https://(?:www\.)?github\.com/([\w.-]+)/([\w.-]+?)(?:\.git)?/?$")


def to_ssh_url(url: str) -> str:
    """``https://github.com/owner/repo(.git)`` as ``git@github.com:owner/repo.git``; any other
    URL (already ssh, another host) is returned unchanged."""
    m = _HTTPS_GITHUB.match(url)
    return f"git@github.com:{m.group(1)}/{m.group(2)}.git" if m else url


def key_path(home: Path | None = None) -> Path:
    """Where the private key lives: ``<sandbox home>/.abcd/github_deploy_key``."""
    base = home if home is not None else Path(os.environ.get("HOME") or Path.home())
    return base / KEY_DIR_NAME / KEY_FILE_NAME


def normalize_key(raw: str) -> str:
    """The key text as an OpenSSH private key file: real newlines (a vault form may hold literal
    ``\\n``), no carriage returns, one trailing newline. Never echoes the value in an error."""
    text = raw.strip().replace("\r\n", "\n").replace("\r", "\n")
    if "\n" not in text and "\\n" in text:
        text = text.replace("\\n", "\n")
    first_line = text.splitlines()[0] if text else ""
    if "PRIVATE KEY" not in first_line:
        raise UsageError(
            "GITHUB_DEPLOY_KEY does not look like a private key (expected a '-----BEGIN ... "
            "PRIVATE KEY-----' block); check the SANDBOX_GITHUB_DEPLOY_KEY vault secret"
        )
    return text + "\n"


def write_key_file(raw: str, home: Path | None = None) -> Path:
    """Write the key to :func:`key_path` with mode 0600 (created private, never widened) and
    return the path. An identical existing file is kept."""
    text = normalize_key(raw)
    path = key_path(home)
    try:
        if path.is_file() and path.read_text() == text:
            os.chmod(path, 0o600)
            return path
    except OSError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(text)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return path


def ssh_command(key_file: Path) -> str:
    """The ``GIT_SSH_COMMAND`` value: only this key, host keys learned on first contact."""
    return (
        f"ssh -i {shlex.quote(str(key_file))} -o IdentitiesOnly=yes "
        "-o StrictHostKeyChecking=accept-new -o BatchMode=yes"
    )


def git_ssh_env(key_file: Path) -> dict[str, str]:
    return {GIT_SSH_VAR: ssh_command(key_file), "GIT_TERMINAL_PROMPT": "0"}


def deploy_key_from_env(environ: Mapping[str, str]) -> str:
    return (environ.get(VAR_DEPLOY_KEY) or environ.get(f"SANDBOX_{VAR_DEPLOY_KEY}") or "").strip()
