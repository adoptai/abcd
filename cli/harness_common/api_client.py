#!/usr/bin/env python3
"""
HTTP client for adoptwebui's end-user agent-harness API (Bearer JWT path).

All routes live under one base URL (ADOPT_WEBUI_ENDPOINT):
    /v1/users/api-token                           -- PAT -> JWT exchange (see auth.py)
    /v1/end-user/agent-harness/{skills,turns,...}  -- skills, turns, trace
    /v1/docstore/{stores,...}                      -- docstore stores + uploads (seed data)
    /v1/org/workstreams                            -- workstream CRUD

This is deliberately the END-USER path (real Bearer JWT, real
workstream-access checks) rather than adoptai-workflows' internal
X-Workflows-Secret path -- see the plan doc for why: the internal path
bypasses exactly the layer where "works locally, breaks on the harness"
bugs tend to live.

Every method returns the parsed JSON body on success and raises
HarnessAPIError with the raw response body on failure, so callers see the
platform's real validation message (e.g. a skill upload 422) instead of a
generic "it failed."
"""

import json
import os
from collections.abc import Iterator
from typing import Any

import requests
from dotenv import load_dotenv


class HarnessAPIError(RuntimeError):
    def __init__(self, method: str, url: str, status_code: int, body: str) -> None:
        self.method = method
        self.url = url
        self.status_code = status_code
        self.body = body
        super().__init__(f"{method} {url} -> {status_code}: {body}")


class HarnessAPIClient:
    """Client for adoptwebui's end-user agent-harness API."""

    def __init__(self, bearer_token: str, webui_endpoint: str) -> None:
        self._bearer_token = bearer_token
        self.webui_endpoint = webui_endpoint.rstrip("/")

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._bearer_token}",
            "Content-Type": "application/json",
        }

    def _url(self, path: str) -> str:
        return f"{self.webui_endpoint}{path}"

    def _request(self, method: str, path: str, timeout: int = 60, **kwargs: Any) -> Any:
        url = self._url(path)
        response = requests.request(method, url, headers=self.headers, timeout=timeout, **kwargs)
        if response.status_code >= 400:
            raise HarnessAPIError(method, url, response.status_code, response.text)
        if not response.content:
            return {}
        return response.json()

    # -- Skills ---------------------------------------------------------

    def upload_skill(
        self,
        skill_name: str,
        skill_md_b64: str,
        aux_files: list[dict[str, str]] | None = None,
        replace: bool = False,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/v1/end-user/agent-harness/skills",
            json={
                "skill_name": skill_name,
                "skill_md_b64": skill_md_b64,
                "aux_files": aux_files or [],
                "replace": replace,
            },
        )

    def get_skill(self, skill_name: str) -> dict[str, Any]:
        return self._request("GET", f"/v1/end-user/agent-harness/skills/{skill_name}")

    def list_skills(self) -> dict[str, Any]:
        return self._request("GET", "/v1/end-user/agent-harness/skills")

    # -- Workstreams ------------------------------------------------------

    def create_workstream(self, name: str, description: str | None = None) -> dict[str, Any]:
        return self._request(
            "POST",
            "/v1/org/workstreams",
            json={"name": name, "description": description},
        )

    def list_workstreams(self, search: str | None = None) -> dict[str, Any]:
        params = {"search": search} if search else {}
        return self._request("GET", "/v1/org/workstreams", params=params)

    # -- Docstore (seed data) ---------------------------------------------

    def create_store(self, name: str, workstream_id: str | None = None) -> dict[str, Any]:
        return self._request(
            "POST",
            "/v1/docstore/stores",
            json={"name": name, "workstream_id": workstream_id},
        )

    def link_store_to_workstreams(
        self, store_id: str, workstream_ids: list[str]
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/v1/docstore/stores/{store_id}/workstreams",
            json={"workstream_ids": workstream_ids},
        )

    def get_upload_urls(
        self,
        store_id: str,
        files: list[dict[str, Any]],
        override: bool = False,
        embed: bool = False,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/v1/docstore/stores/{store_id}/upload-urls",
            json={"files": files, "override": override, "embed": embed},
        )

    def confirm_upload(self, store_id: str, batch_id: str) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/v1/docstore/stores/{store_id}/upload-confirm",
            json={"batch_id": batch_id},
        )

    # -- Turns --------------------------------------------------------------

    def start_turn(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/v1/end-user/agent-harness/turns", json=payload)

    def stop_turn(self, turn_id: str) -> dict[str, Any]:
        return self._request("POST", f"/v1/end-user/agent-harness/turns/{turn_id}/stop")

    def turn_status(self, turn_id: str) -> dict[str, Any]:
        return self._request("GET", f"/v1/end-user/agent-harness/turns/{turn_id}/status")

    def turn_temporal_history(self, turn_id: str) -> dict[str, Any]:
        return self._request(
            "GET", f"/v1/end-user/agent-harness/turns/{turn_id}/temporal-history"
        )

    def turn_trace(self, turn_id: str) -> dict[str, Any]:
        # NOTE: the webui end-user route is /trace/{turn_id}, NOT
        # /turns/{turn_id}/trace -- that path only exists on
        # adoptai-workflows' internal API.
        return self._request("GET", f"/v1/end-user/agent-harness/trace/{turn_id}")

    def stream_turn(self, turn_id: str) -> Iterator[dict[str, Any]]:
        """
        Stream a turn's NDJSON events live. Yields one parsed envelope dict
        per line: {"source": ..., "event": {"type": ..., "data": {...}}}.

        Blocks until the connection closes -- the harness closes the stream
        once the terminal event (harness_turn_complete / harness_error) is
        sent.
        """
        url = self._url(f"/v1/end-user/agent-harness/turns/{turn_id}/stream")
        with requests.get(url, headers=self.headers, stream=True, timeout=(30, 600)) as response:
            if response.status_code >= 400:
                raise HarnessAPIError("GET", url, response.status_code, response.text)
            for line in response.iter_lines(decode_unicode=True):
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue


def get_harness_client_for_env(env_name: str | None = None) -> HarnessAPIClient:
    """
    Build a HarnessAPIClient using the active (or named) environment's
    ADOPT_WEBUI_ENDPOINT + harness PAT credentials.

    Raises:
        ValueError: If no active environment or ADOPT_WEBUI_ENDPOINT is unset.
    """
    from cli.harness_common.auth import get_harness_bearer_token_for_env
    from cli.wdl_common.workspace_manager import (
        DEFAULT_ENV,
        WORKSPACES_DIR,
        get_workspace_manager,
    )

    manager = get_workspace_manager()
    env = env_name or manager.active_env or DEFAULT_ENV
    env_dotenv = WORKSPACES_DIR / env / ".env"
    if env_dotenv.exists():
        load_dotenv(env_dotenv, override=True)

    webui_endpoint = os.getenv("ADOPT_WEBUI_ENDPOINT")
    if not webui_endpoint:
        raise ValueError(
            "ADOPT_WEBUI_ENDPOINT is required (the base URL of the Adopt webui backend). "
            f"Set it in workspaces/{env}/.env."
        )

    token = get_harness_bearer_token_for_env(env)
    return HarnessAPIClient(bearer_token=token, webui_endpoint=webui_endpoint)
